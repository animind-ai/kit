from .base import Built, Widget
from .chips import Chips
from .diff_table import DiffTable
from .problem_card import ProblemCard
from .summary import Summary
from .think_box import ThinkBox

REGISTRY: dict[str, type[Widget]] = {
    "problem_card": ProblemCard,
    "chips": Chips,
    "think_box": ThinkBox,
    "summary": Summary,
    "diff_table": DiffTable,
}


def lookup(name: str) -> type[Widget] | None:
    return REGISTRY.get(name.split("@", 1)[0])


__all__ = ["Built", "Widget", "REGISTRY", "lookup"]
