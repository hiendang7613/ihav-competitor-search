"""Chatbot and full verification stages remain unavailable; homepage and visit adapters ship."""
from typing import Protocol


class ChatbotStage(Protocol):
    def ask(self, prompt: str, providers: list[str]): ...


class HomepageStage(Protocol):
    def check(self, candidate: dict): ...


class VisitStage(Protocol):
    def lookup(self, lookup_host: str): ...


class VerificationStage(Protocol):
    def verify(self, candidate: dict): ...


class UnavailableStages:
    def ask(self, *args, **kwargs):
        raise NotImplementedError("Chatbot integration requires the released child capability contract")

    def verify(self, *args, **kwargs):
        raise NotImplementedError("Verification requires the host agent and official-page evidence")
