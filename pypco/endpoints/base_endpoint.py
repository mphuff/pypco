"""Provides endpoint classes.

All endpoints inherit from the BaseEndpoint class, which
provides most functionality. All functionality is available
to be overwritten by each endpoint subclass.
"""

import logging
import base64
import time
import re
import requests
from .utils import PCOAuthType

# Model imports
from ..models import people #pylint: disable=W0611

class PCOAPIMethod(): #pylint: disable=R0903
    """Defines API endpoint HTTP method types."""

    GET = 'GET'
    POST = 'POST'
    PATCH = 'PATCH'
    DELETE = 'DELETE'

class BaseEndpoint:
    """The base endpoint class from which all other endpoints inherit.

    The BaseEndpoint class handles most of the grunt work for the API wrapper.
    This class is responsible for executing and managing API requests. This
    functionality could be accomplished with a single class. However, we're
    using inheritance because this would allow us to override any bit of
    functionality if needed in the future without changing code that would affect
    all endpoints.
    """

    API_BASE = "https://api.planningcenteronline.com"

    def __init__(self, auth_config):
        """Initialize the BasePCOEndpoint."""

        self._auth_config = auth_config
        self._auth_header = None
        self._log = logging.getLogger(__name__)

        self._log.debug("Initialized the %s endpoint.", type(self).__name__)

        for subclass in self.__class__.__subclasses__():
            setattr(self, subclass.resolve_class_name_url(), subclass(auth_config))

    def _get_auth_header(self):
        """Get the authorization header for the request."""

        # We cache the header so we don't have to generate
        # multiple times.
        if self._auth_header is None:

            self._log.debug("Didn't find cached auth header. Building now.")

            if self._auth_config.auth_type == PCOAuthType.PAT:
                self._auth_header = "Basic {}".format(
                    base64.b64encode(
                        '{}:{}'.format(
                            self._auth_config.application_id,
                            self._auth_config.secret
                        ).encode()
                    ).decode()
                )

            elif self._auth_config.auth_type == PCOAuthType.OAUTH:
                self._auth_header = "Bearer {}".format(self._auth_config.token)

        return self._auth_header

    def _dispatch_single_request(self, url, params=None, payload=None, method=PCOAPIMethod.GET):
        """Dispatches PCO requests to the API.

        Intelligently handles rate limiting; if a rate limit response is received,
        sleep the current thread for the duration specified in the 'Retry-After' header.

        Args:
            url (str): PCO URI to be called (everything after API_BASE)
            params (dict): URL parameters to be dispatched in the request.
            payload (dict): The payload to be sent in the request (must be serializable to JSON).

        Returns:
            The result of the request parsed from response JSON. The idea is that
            it will be possible to feed this directly to a model to build an object
            representing the object in PCO.

        Raises:
            HTTPError: Raised by the requests module if there's an error we can't handle.
        """

        while True:

            self._log.debug("Executing request to: \"%s\"", url)

            response = requests.request(
                method,
                url,
                params=params,
                json=payload,
                headers={
                    "Authorization": self._get_auth_header()
                }
            )

            self._log.debug("Request response code was: %d", response.status_code)

            if response.status_code == 429:
                self._log.debug(
                    "Received rate limit response. Sleep for: %s second(s)",
                    response.headers['Retry-After']
                )

                time.sleep(int(response.headers['Retry-After']))
                continue

            response.raise_for_status()

            self._log.debug("Response content: %s", response.json())

            break

        return response.json()

    def get(self, item_id):
        """Get a single object from the API endpoint based on ID

        Note: This should not be called from the BaseModel class. It should be called
        from a class that inherits from the BaseModel class.
            >>> p = pypco.PCO('app_id', 'secret')
            >>> person = p.people.people.get("12345")

        Args:
            item_id (str): The object's unique ID

        Returns:
            BaseModel: an object that inherits from the BaseModel class.
        """

        obj = self._dispatch_single_request(
            "{}/{}".format(
                self.get_full_endpoint_url(),
                item_id
            )
        )

        klass_info = self.resolve_model_type(obj['data'])

        klass = getattr(globals()[klass_info[0]], klass_info[1])

        return klass(self, obj, from_get=True)

    # TODO: Add list function to get multiple objects (returns iterator, handles paging)

    # TODO: Add function to save objects

    # TODO: Add function to create objects

    # TODO: Add function to delete objects

    # TODO: Figure out file uploads

    @classmethod
    def resolve_root_endpoint_name(cls):
        """Resolve the current root endpoint name.

        Raises:
        NotValidRootEndpointError: This is a child endpoint, not a root endpoint.

        Returns:
        The name of the root endpoint represented by this object.
        """

        if not cls.is_root_endpoint():
            raise NotValidRootEndpointError("This is a child endpoint, not a root endpoint!")

        return cls.__name__[:-8].lower()

    @classmethod
    def is_root_endpoint(cls):
        """Check whether or not the current endpoint is a root endoint."""

        return cls.__name__[-8:] == 'Endpoint'

    @classmethod
    def resolve_class_name_url(cls):
        """Resolve the current CamelCase class name to snake_case for URLs."""

        # If "Endpoint" is in the class, it's top-level; append v2
        if cls.is_root_endpoint():
            return "{}/v2".format(cls.resolve_root_endpoint_name())

        # If we have multiple capitals, convert to snake case
        if len(re.findall(r'[A-Z]', cls.__name__)) > 1:
            return re.subn(r'(?!^)([A-Z])', r'_\1', cls.__name__)[0].lower()

        # Otherwise, just return the endpoint name in lowercase
        return cls.__name__.lower()

    @classmethod
    def get_full_endpoint_url(cls):
        """Get the full url for the current endpoint."""

        if cls.__name__ == 'BaseEndpoint':
            return cls.API_BASE

        return "{}/{}".format(
            cls.__bases__[0].get_full_endpoint_url(), #pylint: disable=E1101,W0212
            cls.resolve_class_name_url()
        )

    @classmethod
    def resolve_model_type(cls, data):
        """Given an object from the PCO API, resolve object type.

        Returns:
            tuple (module_name, class_name): A tuple to feed into getattr to
            get a reference to the class.
        """

        return (
            cls.__bases__[0].resolve_root_endpoint_name(), #pylint: disable=E1101
            data['type']
        )

    @staticmethod
    def _check_rate_limit_response(response):
        """Checks for a rate limiting response from PCO.

        Returns:
            None if the API response is not a rate limit response. Otherwise,
            returns the contents of the "Retry-After" header which can be used
            to delay further requests until rate limiting has expired.
        """

        if response.status_code == 429:
            return int(response.headers['Retry-After'])

        return None

class NotValidRootEndpointError(Exception):
    """Exception raised when we attempt to find a root endpoint name for a non-root endpoint."""

    pass