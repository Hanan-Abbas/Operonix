import abc

class BaseTool(abc.ABC):
    def __init__(self, name):
        self.name = name

    @abc.abstractmethod
    async def run(self, action, args):
        """All tools must implement this entry point."""
        pass