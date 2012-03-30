# -*- test-case-name: vumi.middleware.tests.test_base -*-

from twisted.internet.defer import inlineCallbacks, returnValue

from vumi.utils import load_class_by_string
from vumi.errors import ConfigError


class BaseMiddleware(object):
    """Common middleware base class.

    This is a convenient repository for commonalities between the various
    middlewares. You should not subclass or instantiate this directly.
    """

    def __init__(self, name, config, worker):
        self.name = name
        self.config = config
        self.worker = worker

    def setup_middleware(self):
        pass

    def handle_inbound(self, message, endpoint):
        return message

    def handle_outbound(self, message, endpoint):
        return message

    def handle_event(self, message, endpoint):
        return message

    def handle_failure(self, message, endpoint):
        return message


class TransportMiddleware(BaseMiddleware):
    """Message processor middleware for Transports.
    """


class ApplicationMiddleware(BaseMiddleware):
    """Message processor middleware for Applications.
    """


class MiddlewareStack(object):
    """Ordered list of middlewares to pass a Message through.
    """

    def __init__(self, middlewares):
        self.middlewares = middlewares

    @inlineCallbacks
    def _handle(self, middlewares, handler_name, message, endpoint):
        method_name = 'handle_%s' % (handler_name,)
        for middleware in middlewares:
            handler = getattr(middleware, method_name)
            message = yield handler(message, endpoint)
        returnValue(message)

    def apply_consume(self, handler_name, message, endpoint):
        return self._handle(
            self.middlewares, handler_name, message, endpoint)

    def apply_publish(self, handler_name, message, endpoint):
        return self._handle(
            reversed(self.middlewares), handler_name, message, endpoint)


def create_middlewares_from_config(worker, config):
    """Return a list of middleware objects created from a worker
       configuration.
       """
    middlewares = []
    for item in config.get("middleware", []):
        keys = item.keys()
        if len(keys) != 1:
            raise ConfigError("Middleware items contain only a single"
                              " key-value pair. The key should be a name"
                              " for the middleware. The value should be"
                              " the full dotted name of the class"
                              " implementing the middleware.")
        middleware_name = keys[0]
        cls_name = item[middleware_name]
        middleware_config = config.get(middleware_name, {})
        cls = load_class_by_string(cls_name)
        middleware = cls(middleware_name, middleware_config, worker)
        middlewares.append(middleware)
    return middlewares


@inlineCallbacks
def setup_middlewares_from_config(worker, config):
    """Create a list of middleware objects, call .setup_middleware() on
       then and then return the list.
       """
    middlewares = create_middlewares_from_config(worker, config)
    for mw in middlewares:
        yield mw.setup_middleware()
    returnValue(middlewares)
