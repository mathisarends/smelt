from .inner import Hidden

def __getattr__(name):
    raise AttributeError(name)
