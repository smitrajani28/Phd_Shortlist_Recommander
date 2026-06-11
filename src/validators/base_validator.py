"""
Abstract base class for all supervisor validators.
"""

from abc import ABC, abstractmethod
from ..models.supervisor import Supervisor


class BaseValidator(ABC):
    """Interface that every validator must implement."""

    @abstractmethod
    def validate(self, supervisor: Supervisor) -> bool:
        """
        Determine whether a supervisor passes this validation gate.

        Args:
            supervisor: Supervisor candidate to evaluate.

        Returns:
            True if the supervisor passes, False to exclude them.
        """
        ...
