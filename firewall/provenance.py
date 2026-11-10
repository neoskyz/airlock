from .models import Source


class Provenance:
    """Host-owned ledger. Tool output never becomes a trusted user instruction."""

    def __init__(self, user_message: str):
        self.sources = [Source(id="user:0", origin="user", name="user input", text=user_message)]

    def add_tool(self, name: str, text: str) -> Source:
        source = Source(id=f"tool:{len(self.sources)}", origin="tool", name=name, text=text)
        self.sources.append(source)
        return source

    @property
    def user_message(self):
        return self.sources[0].text

    def serialize(self):
        return [source.model_dump() for source in self.sources]
