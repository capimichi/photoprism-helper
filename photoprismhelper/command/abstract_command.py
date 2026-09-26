from __future__ import annotations

from abc import ABC, abstractmethod
import click


class AbstractCommand(ABC):
    command_name: str = "command"

    def register_options(self, fn):
        return fn

    @abstractmethod
    def run(self, *args, **kwargs):
        raise NotImplementedError

    def to_click_command(self) -> click.Command:
        @click.command(name=self.command_name, help=(self.run.__doc__ or "").strip())
        @self.register_options
        def command(**kwargs):
            self.run(**kwargs)

        return command
