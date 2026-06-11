"""
Abstract base class for all supervisor retrievers.
Enforces a common interface so the pipeline can swap sources easily (OCP).
"""

from abc import ABC, abstractmethod
from ..models.student import StudentProfile
from ..models.supervisor import Supervisor


class BaseRetriever(ABC):
    """Interface that every retriever must implement."""

    @abstractmethod
    def retrieve(self, profile: StudentProfile) -> list[Supervisor]:
        """
        Fetch candidate supervisors relevant to the student profile.

        Args:
            profile: Parsed and validated student profile.

        Returns:
            List of partially-populated Supervisor objects.
        """
        ...
