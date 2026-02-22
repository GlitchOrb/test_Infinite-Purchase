from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import pandas as pd


class IndicatorBase(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def inputs_required(self) -> Iterable[str]:
        raise NotImplementedError

    @property
    @abstractmethod
    def render_location(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def compute(self, dataframe: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def validate_inputs(self, dataframe: pd.DataFrame) -> None:
        missing = [c for c in self.inputs_required if c not in dataframe.columns]
        if missing:
            raise RuntimeError(f"{self.name} missing input columns: {missing}")
