import logging

try:
    import os
except ImportError:
    pass
else:
    import sys
finally:
    import json

VALUE = 1
_logger = logging.getLogger(__name__)
