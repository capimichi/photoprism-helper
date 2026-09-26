# photoprism-helper

CLI e tool di supporto per **PhotoPrism** che indicizza foto e video in un database esterno **MariaDB**. Permette di analizzare lo spazio occupato, identificare i file più pesanti, assegnare tag intelligenti basati sulla cartella di origine e ottimizzare i video pesanti mantenendo intatti metadati ed EXIF.

---

## Funzionalità Principali

1. **Indicizzazione su MariaDB**:
   - Sincronizzazione automatica tramite le REST API di PhotoPrism.
   - Catalogazione di metadati completi: dimensioni file, estensione, tipologia (`image`, `video`, ecc.), risoluzione, durata, codec, data di scatto e cartella di origine.
2. **Analisi dello Storage**:
   - Report tabellare di occupazione per tipologia (immagini vs video vs altri).
   - Ripartizione dello spazio per estensione (`.mp4`, `.mov`, `.heic`, `.jpg`, `.raw`, ecc.).
   - Classifica delle cartelle e dei singoli file più pesanti.
3. **Tagging da Cartelle di Origine**:
   - Estrazione automatica dei nomi di eventi o cartelle (es. `2023/Vacanze_Sicilia/` $\to$ tag `Vacanze Sicilia`).
   - Modalità dry-run e applicazione automatica dei tag direttamente in PhotoPrism.
4. **Ottimizzazione Video (FFmpeg H.265/AV1)**:
   - Identificazione automatica dei video di grandi dimensioni (es. registrazioni 4K/60fps non compresse).
   - Stima del risparmio di spazio (~50-70%).
   - Generazione di script FFmpeg che preservano integralmente stream audio, timestamp originali e tutti i metadati/EXIF (`-map 0 -map_metadata 0`).

---

## Architettura del Progetto

Il progetto segue la struttura a componenti disaccoppiati (con Dependency Injection tramite `injector`):

```
photoprism-helper/
├── photoprismhelper/
│   ├── client/         # PhotoprismClient (REST API, session/token auth)
│   ├── command/        # Comandi CLI Click (db:init, media:sync, media:stats, ...)
│   ├── config/         # AppConfig dataclass
│   ├── container/      # DefaultContainer (Dependency Injection con injector)
│   ├── entity/         # Modelli SQLAlchemy (Base, MediaItem)
│   ├── manager/        # DbManager (MariaDB/MySQL engine, sessioni e transazioni)
│   ├── mapper/         # MediaMapper (mapping payload API -> entità DB, parser tag)
│   ├── model/          # DTOs e modelli statistici (StorageSummary, BreakdownStat, ...)
│   ├── repository/     # BaseRepository e MediaRepository (query di analisi e upsert)
│   ├── service/        # MediaSyncService, StorageAnalysisService, TagService, VideoOptimizerService
│   └── cli.py          # Entrypoint principale Click
├── docker/
│   └── python/Dockerfile
├── docker-compose.yml  # MariaDB 10.11 + Container Python
├── pyproject.toml      # Configurazione pacchetto e dipendenze
├── .env.example        # Modello variabili d'ambiente
└── README.md
```

---

## Requisiti

- **Python**: `>= 3.11` (consigliato 3.12)
- **MariaDB** o **MySQL**: `>= 10.5` / `8.0` (incluso in `docker-compose.yml`)
- **FFmpeg**: facoltativo per l'ottimizzazione video (già incluso nel container Docker)

---

## Installazione e Avvio Rapido

### Opzione A: Con Docker Compose (Consigliata)

1. Clona il repository e prepara l'ambiente:
   ```bash
   cp .env.example .env
   # Modifica .env con i dati di accesso al tuo PhotoPrism
   ```

2. Avvia i container:
   ```bash
   docker compose up -d
   ```
   MariaDB sarà operativo sulla porta `3306` e le tabelle verranno inizializzate automaticamente.

3. Esegui i comandi tramite `docker compose exec`:
   ```bash
   docker compose exec python photoprismhelper --help
   docker compose exec python photoprismhelper media:sync
   docker compose exec python photoprismhelper media:stats
   ```

---

### Opzione B: Setup Locale

1. Crea ed attiva un virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Installa le dipendenze in modalità editable:
   ```bash
   pip install -e .
   ```

3. Configura le variabili:
   ```bash
   cp .env.example .env
   ```

4. Inizializza il database MariaDB:
   ```bash
   photoprismhelper db:init
   ```

---

## Configurazione (`.env`)

| Variabile | Descrizione | Default |
|-----------|-------------|---------|
| `DATABASE_URL` | Stringa SQLAlchemy per MariaDB | `mysql+pymysql://photoprismhelper:photoprismhelper@localhost:3306/photoprismhelper` |
| `PHOTOPRISM_BASE_URL` | URL dell'istanza PhotoPrism | `http://localhost:2342` |
| `PHOTOPRISM_USERNAME` | Username per login PhotoPrism | `admin` |
| `PHOTOPRISM_PASSWORD` | Password di PhotoPrism | `insecure` |
| `PHOTOPRISM_API_TOKEN` | Token API opzionale (alternativo a user/pass) | `""` |
| `PHOTOPRISM_ORIGINALS_PATH` | Cartella host contenente i file originali | `/photoprism/originals` |

---

## Guida ai Comandi CLI

### 1. Inizializzazione Database
Crea la tabella `media_items` e gli indici su MariaDB se non esistono:
```bash
photoprismhelper db:init
```

### 2. Sincronizzazione Media (`media:sync`)
Estrae tutte le foto e i video da PhotoPrism e popola MariaDB:
```bash
# Sincronizzazione completa
photoprismhelper media:sync

# Sincronizzazione con batch size personalizzato o limite massimo di test
photoprismhelper media:sync --batch-size 200 --max-items 500
```

### 3. Analisi Storage (`media:stats`)
Visualizza una panoramica completa:
- Spazio totale suddiviso per tipo (immagini vs video)
- Classifica delle estensioni più pesanti
- Classifica delle cartelle più capienti
- Elenco dei file più pesanti
```bash
photoprismhelper media:stats

# Mostra i 30 video più pesanti
photoprismhelper media:stats --top 30 --type video
```

### 4. Suggerimento e Applicazione Tag da Cartella (`tag:suggest`)
Analizza i percorsi di importazione e propone l'aggiunta di tag intelligenti:
```bash
# Anteprima (dry-run)
photoprismhelper tag:suggest --limit 50

# Applicazione diretta a PhotoPrism
photoprismhelper tag:suggest --limit 50 --apply
```

### 5. Ottimizzazione Video Pesanti (`optimize:videos`)
Trova i video oltre una certa dimensione (es. 50 MB o 100 MB), calcola il risparmio stimato (~50%) e può generare uno script Bash per la ricodifica con FFmpeg (H.265 CRF 26) preservando metadati e date:
```bash
# Anteprima candidati oltre 100 MB
photoprismhelper optimize:videos --min-size-mb 100

# Genera lo script bash per eseguire l'ottimizzazione in batch
photoprismhelper optimize:videos --min-size-mb 100 --generate-script optimize_videos.sh
```

Lo script generato:
- Esegue FFmpeg con `-map 0 -map_metadata 0 -tag:v hvc1 -c:a copy`
- Preserva la data originale di scatto (`touch -r`)
- Salva un backup di sicurezza dell'originale (`.bak`) prima della sostituzione.

---

## Licenza

MIT
