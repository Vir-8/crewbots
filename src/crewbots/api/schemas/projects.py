"""The user's projects, and what Claude Code and Codex already know, for getting a crew started."""


from pydantic import BaseModel


class ProjectBody(BaseModel):
    folder: str
    about: str = ""

class ProjectOut(BaseModel):
    name: str
    folder: str
    about: str
    missing: bool  # its folder isn't there any more

class ProjectsOut(BaseModel):
    projects: list[ProjectOut]
    errors: dict[str, str]  # project files that couldn't be read, and why

class FoundMemoryOut(BaseModel):
    path: str
    source: str  # claude or codex
    title: str

class FoundFolderOut(BaseModel):
    folder: str
    name: str  # a name for it as a project
    about: str
    sources: list[str]
    sessions: int
    last_used: str
    memories: list[FoundMemoryOut]
    project: str | None  # the project it already is, if it is one

class FoundOut(BaseModel):
    folders: list[FoundFolderOut]
    memories: list[FoundMemoryOut]  # not about one folder (Codex keeps one set)

class NewProject(BaseModel):
    name: str
    folder: str
    about: str = ""

class BringIn(BaseModel):
    projects: list[NewProject] = []
    memories: list[str] = []  # paths, from what was found

class BroughtIn(BaseModel):
    projects: list[ProjectOut]
    memories: int  # how many went to the chief
    task_id: str | None  # the chief's chat, where it's learning from them
