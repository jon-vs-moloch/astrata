"""Constitution loading and parsing helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel


class ConstitutionSection(BaseModel):
    title: str
    level: int
    content: str
    subsections: List["ConstitutionSection"] = []


class Constitution(BaseModel):
    text: str
    sections: Dict[str, ConstitutionSection]

    @classmethod
    def from_text(cls, text: str) -> "Constitution":
        sections = cls._parse_sections(text)
        return cls(text=text, sections={s.title: s for s in sections})

    @staticmethod
    def _parse_sections(text: str) -> List[ConstitutionSection]:
        lines = text.split("\n")
        sections = []
        current_section = None
        current_content = []

        for line in lines:
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if header_match:
                # Save previous section
                if current_section:
                    current_section.content = "\n".join(current_content).strip()
                    sections.append(current_section)

                level = len(header_match.group(1))
                title = header_match.group(2).strip()
                current_section = ConstitutionSection(title=title, level=level, content="")
                current_content = []
            else:
                if current_section:
                    current_content.append(line)

        # Save last section
        if current_section:
            current_section.content = "\n".join(current_content).strip()
            sections.append(current_section)

        return sections

    def get_section(self, title: str) -> Optional[ConstitutionSection]:
        return self.sections.get(title)

    def get_design_principles(self) -> List[str]:
        section = self.get_section("Design Principles")
        if not section:
            return []
        # Parse the numbered principles
        principles = []
        lines = section.content.split("\n")
        current_principle = []
        for line in lines:
            if re.match(r"### \d+\.", line):
                if current_principle:
                    principles.append("\n".join(current_principle).strip())
                    current_principle = []
                current_principle.append(line)
            elif current_principle:
                current_principle.append(line)
        if current_principle:
            principles.append("\n".join(current_principle).strip())
        return principles

    def get_core_thesis(self) -> Optional[str]:
        section = self.get_section("Core Thesis")
        return section.content if section else None


def load_constitution_text(project_root: Path) -> str:
    spec_path = project_root / "spec.md"
    return spec_path.read_text() if spec_path.exists() else ""


def load_constitution(project_root: Path) -> Constitution:
    text = load_constitution_text(project_root)
    return Constitution.from_text(text)
