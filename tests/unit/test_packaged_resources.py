from pathlib import Path
import tomllib


def test_every_runtime_prompt_is_included_in_the_installed_wheel():
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as source:
        included = tomllib.load(source)["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for prompt in (root / "prompts").glob("*.md"):
        relative = prompt.relative_to(root).as_posix()
        assert included.get(relative) == f"interview_intelligence/resources/{relative}", relative
