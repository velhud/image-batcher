from __future__ import annotations

import itertools
from typing import List

from app.models import PromptTemplate


def expand_template(template: PromptTemplate) -> List[str]:
    """Expand template by cartesian product of variable values."""
    keys = list(template.variables.keys())
    if not keys:
        return [template.template]
    values_lists = [template.variables[k] for k in keys]
    prompts: List[str] = []
    for combo in itertools.product(*values_lists):
        filled = template.template
        for key, val in zip(keys, combo):
            filled = filled.replace("{" + key + "}", str(val))
        prompts.append(filled)
    return prompts
