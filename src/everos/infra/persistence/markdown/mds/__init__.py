"""Business markdown frontmatter schemas (mds = "markdown schemas").

Each business record kind that is stored as markdown gets a concrete
frontmatter class here, subclassing one of the chassis classes from
:mod:`everos.core.persistence.markdown`:

    * :class:`UserScopedFrontmatter` for user-track records
    * :class:`AgentScopedFrontmatter` for agent-track records
    * :class:`BaseFrontmatter` for scope-agnostic records (rare)

Schemas drive path resolution via ClassVars; each storage strategy has
its own conventions:

- **Daily-log** schemas declare ``ENTRY_ID_PREFIX`` (token in
  ``<prefix>_<date>_<seq>``), ``DIR_NAME`` (sub-directory under
  ``<scope>/<id>/``) and ``FILE_PREFIX`` (leading token of the daily
  filename joined with ``-<YYYY-MM-DD>.md``).
- **Skill** schemas (:class:`AgentSkillFrontmatter`) pin the directory
  layout via five ``SKILL_*`` ClassVars (container / dir prefix /
  main filename / references / scripts).
- **Profile** schemas declare ``PROFILE_FILENAME`` (``"user.md"`` /
  ``"agent.md"`` / …) and inherit ``SCOPE_DIR`` from a scope mixin; no
  profile base class — the writer/reader pair is duck-typed.
"""

from .agent_case import AgentCaseDailyFrontmatter as AgentCaseDailyFrontmatter
from .agent_skill import AgentSkillFrontmatter as AgentSkillFrontmatter
from .atomic_fact import AtomicFactDailyFrontmatter as AtomicFactDailyFrontmatter
from .episode import EpisodeDailyFrontmatter as EpisodeDailyFrontmatter
from .foresight import ForesightDailyFrontmatter as ForesightDailyFrontmatter
from .profile import UserProfileFrontmatter as UserProfileFrontmatter
from .v2_memory import V2CaseFrontmatter as V2CaseFrontmatter
from .v2_memory import V2EpisodeFrontmatter as V2EpisodeFrontmatter
from .v2_memory import V2PlaybookFrontmatter as V2PlaybookFrontmatter

__all__ = [
    "AgentCaseDailyFrontmatter",
    "AgentSkillFrontmatter",
    "AtomicFactDailyFrontmatter",
    "EpisodeDailyFrontmatter",
    "ForesightDailyFrontmatter",
    "UserProfileFrontmatter",
    "V2CaseFrontmatter",
    "V2EpisodeFrontmatter",
    "V2PlaybookFrontmatter",
]
