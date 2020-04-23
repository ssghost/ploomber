class DAGRenderError(Exception):
    """Raise when a dag fails to build
    """
    pass


class DAGBuildError(Exception):
    """Raise when a dag fails to build
    """
    pass


class TaskBuildError(Exception):
    """Raise when a task fails to build
    """
    pass


class TaskRenderError(Exception):
    """Raise when a task fails to render
    """
    pass


class RenderError(Exception):
    """Raise when a template fails to render
    """
    pass


class SourceInitializationError(Exception):
    """Raise when a source fails to initialize due to wrong parameters
    """
    pass


class CallbackSignatureError(Exception):
    """When a callback function does not have the right signature
    """
    pass
