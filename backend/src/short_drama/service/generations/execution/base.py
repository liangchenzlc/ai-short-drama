from short_drama.service.generations.results.archive import GenerationArchive
from short_drama.service.generations.runtime.lifecycle import TaskRuntimeService


class ExecutionServiceBase:
    def __init__(self, factory, settings, gateway, storage):
        self.factory, self.settings = factory, settings
        self.gateway, self.storage = gateway, storage
        self.store = TaskRuntimeService(factory, settings)
        self.archive = GenerationArchive(factory, settings, gateway, storage)
