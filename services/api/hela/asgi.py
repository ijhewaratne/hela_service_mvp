from .app import create_app
from .core.config import get_settings
from .core.logs import configure_logging

configure_logging(get_settings(), 'api')
app = create_app()
