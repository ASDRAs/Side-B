class SearchPipelineError(Exception):
    """Base class for expected search pipeline failures."""


class NoResultsError(SearchPipelineError):
    def __init__(self, query: str) -> None:
        self.query = query
        super().__init__(f"No search results for query: {query}")


class SearchTimeoutError(SearchPipelineError):
    def __init__(self, query: str) -> None:
        self.query = query
        super().__init__(f"Search timed out for query: {query}")


class ServiceUnavailableError(SearchPipelineError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
