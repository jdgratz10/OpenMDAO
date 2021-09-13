"""Define the OptionsDictionary class."""

from openmdao.utils.om_warnings import warn_deprecation
from openmdao.core.constants import _UNDEFINED


#
# Template for `check_valid` function
#
def check_valid(name, value):
    """
    Check the validity of value for the option with name.

    Parameters
    ----------
    name : str
        Name of the option.
    value : any
        Value for the option.

    Raises
    ------
    ValueError
        If value is not valid for option.
    """
    raise ValueError(f"Option '{name}' with value {value} is not valid.")


class OptionsDictionary(object):
    """
    Dictionary with pre-declaration of keys for value-checking and default values.

    This class is instantiated for:
        1. the options attribute in solvers, drivers, and processor allocators
        2. the supports attribute in drivers
        3. the options attribute in systems

    Parameters
    ----------
    parent_name : str
        Name or class name of System that owns this OptionsDictionary.
    read_only : bool
        If True, setting (via __setitem__ or update) is not permitted.

    Attributes
    ----------
    _dict : dict of dict
        Dictionary of entries. Each entry is a dictionary consisting of value, values,
        types, desc, lower, and upper.
    _parent_name : str or None
        If defined, prepend this name to beginning of all exceptions.
    _read_only : bool
        If True, no options can be set after declaration.
    _all_recordable : bool
        Flag to determine if all options in UserOptions are recordable.
    """

    def __init__(self, parent_name=None, read_only=False):
        """
        Initialize all attributes.
        """
        self._dict = {}
        self._parent_name = parent_name
        self._read_only = read_only

        self._all_recordable = True

    def __getstate__(self):
        """
        Return state as a dict.

        Returns
        -------
        dict
            State to get.
        """
        if self._all_recordable:
            return self.__dict__
        else:
            state = self.__dict__.copy()
            state['_dict'] = {key: val for key, val in state['_dict'].items() if val['recordable']}
            return state

    def __repr__(self):
        """
        Return a dictionary representation of the options.

        Returns
        -------
        dict
            The options dictionary.
        """
        return self._dict.__repr__()

    def __rst__(self):
        """
        Generate reStructuredText view of the options table.

        Returns
        -------
        list of str
            A rendition of the options as an rST table.
        """
        lines = self.to_table(fmt='rst').split('\n')
        return lines

    def to_table(self, fmt='github', missingval='N/A'):
        """
        Get a table representation of this OptionsDictionary as a table in the requested format.

        Parameters
        ----------
        fmt : str
            The formatting of the requested table.  Options are the same as those available
            to the tabulate package.  See tabulate.tabulate_formats for a complete list.
            Default value of 'github' produces a table in GitHub-flavored markdown.
        missingval : str
            The value to be displayed in place of None.

        Returns
        -------
        str
            A string representation of the table in the requested format.
        """
        try:
            from tabulate import tabulate
        except ImportError as e:
            msg = "'to_table' requires the tabulate package but it is not currently installed." \
                  " Use `pip install tablulate` or install openmdao with" \
                  " `pip install openmdao[notebooks]`."
            raise ImportError(msg)

        tlist = [['Option', 'Default', 'Acceptable Values', 'Acceptable Types', 'Description',
                  'Deprecation']]
        for key in sorted(self._dict.keys()):
            options = self._dict[key]
            default = options['val'] if options['val'] is not _UNDEFINED else '**Required**'
            # if the default is an object instance, replace with the (unqualified) object type
            default_str = str(default)
            idx = default_str.find(' object at ')
            if idx >= 0 and default_str[0] == '<':
                parts = default_str[:idx].split('.')
                default = parts[-1]

            acceptable_values = options['values']
            if acceptable_values is not None:
                if not isinstance(acceptable_values, (set, tuple, list)):
                    acceptable_values = (acceptable_values,)
                acceptable_values = [value for value in acceptable_values]

            acceptable_types = options['types']
            if acceptable_types is not None:
                if not isinstance(acceptable_types, (set, tuple, list)):
                    acceptable_types = (acceptable_types,)
                acceptable_types = [type_.__name__ for type_ in acceptable_types]

            desc = options['desc']

            deprecation = options['deprecation']
            if deprecation is not None:
                tlist.append([key, default, acceptable_values, acceptable_types, desc, deprecation])
            else:
                tlist.append([key, default, acceptable_values, acceptable_types, desc])

        return tabulate(tlist, headers='firstrow', tablefmt=fmt, missingval=missingval)

    def __str__(self, width=100):
        """
        Generate text string representation of the options table.

        Parameters
        ----------
        width : int
            The maximum width of the text.

        Returns
        -------
        str
            A text representation of the options table.
        """
        rst = self.to_table(fmt='rst').split('\n')
        cols = [len(header) for header in rst[0].split()]
        desc_col = sum(cols[:-1]) + 2 * (len(cols) - 1)
        desc_len = width - desc_col

        # if it won't fit in allowed width, just return the rST
        if desc_len < 10:
            return '\n'.join(rst)

        text = []
        for row in rst:
            if len(row) > width:
                text.append(row[:width])
                if not row.startswith('==='):
                    row = row[width:].rstrip()
                    while(len(row) > 0):
                        text.append(' ' * desc_col + row[:desc_len])
                        row = row[desc_len:]
            else:
                text.append(row)

        return '\n'.join(text)

    def _raise(self, msg, exc_type=RuntimeError):
        """
        Raise the given exception type, with parent's name prepended to the message.

        Parameters
        ----------
        msg : str
            The error message.
        exc_type : class
            The type of the exception to be raised.
        """
        if self._parent_name is None:
            raise exc_type(msg)

        raise exc_type(f"{self._parent_name}: {msg}")

    def _assert_valid(self, name, value):
        """
        Check whether the given value is valid, where the key has already been declared.

        The optional checks consist of ensuring: the value is one of a list of acceptable values,
        the type of value is one of a list of acceptable types, value is not less than lower,
        value is not greater than upper, and value satisfies check_valid.

        Parameters
        ----------
        name : str
            The key for the declared option.
        value : object
            The default or user-set value to check for value, type, lower, and upper.
        """
        meta = self._dict[name]

        if not (value is None and meta['allow_none']):
            values = meta['values']
            lower = meta['lower']
            upper = meta['upper']
            types = meta['types']

            # If only values is declared
            if values is not None:
                if value not in values:
                    if isinstance(value, str):
                        value = f"'{value}'"
                    self._raise(f"Value ({value}) of option '{name}' is not one of {values}.",
                                ValueError)
            # If only types is declared
            elif types is not None and not isinstance(value, types):
                vtype = type(value).__name__

                if isinstance(value, str):
                    value = f"'{value}'"

                if isinstance(types, (set, tuple, list)):
                    typs = tuple([type_.__name__ for type_ in types])
                    self._raise(f"Value ({value}) of option '{name}' has type '{vtype}', but one "
                                f"of types {typs} was expected.", exc_type=TypeError)
                else:
                    self._raise(f"Value ({value}) of option '{name}' has type '{vtype}', but type "
                                f"'{types.__name__}' was expected.", exc_type=TypeError)

            if upper is not None and value > upper:
                self._raise(f"Value ({value}) of option '{name}' exceeds maximum allowed value of "
                            f"{upper}.", exc_type=ValueError)
            if lower is not None and value < lower:
                self._raise(f"Value ({value}) of option '{name}' is less than minimum allowed "
                            f"value of {lower}.", exc_type=ValueError)

        # General function test
        if meta['check_valid'] is not None:
            meta['check_valid'](name, value)

    def declare(self, name, default=_UNDEFINED, values=None, types=None, desc='',
                upper=None, lower=None, check_valid=None, allow_none=False, recordable=True,
                deprecation=None):
        r"""
        Declare an option.

        The value of the option must satisfy the following:
        1. If values only was given when declaring, value must be in values.
        2. If types only was given when declaring, value must satisfy isinstance(value, types).
        3. It is an error if both values and types are given.

        Parameters
        ----------
        name : str
            Name of the option.
        default : object or Null
            Optional default value that must be valid under the above 3 conditions.
        values : set or list or tuple or None
            Optional list of acceptable option values.
        types : type or tuple of types or None
            Optional type or list of acceptable option types.
        desc : str
            Optional description of the option.
        upper : float or None
            Maximum allowable value.
        lower : float or None
            Minimum allowable value.
        check_valid : function or None
            User-supplied function with arguments (name, value) that raises an exception
            if the value is not valid.
        allow_none : bool
            If True, allow None as a value regardless of values or types.
        recordable : bool
            If True, add to recorder.
        deprecation : str or None
            If None, it is not deprecated. If a str, use as a DeprecationWarning
            during __setitem__ and __getitem__.
        """
        if values is not None and not isinstance(values, (set, list, tuple)):
            self._raise(f"In declaration of option '{name}', the 'values' arg must be of type None,"
                        f" list, or tuple - not {values}.", exc_type=TypeError)

        if types is not None and not isinstance(types, (type, set, list, tuple)):
            self._raise(f"In declaration of option '{name}', the 'types' arg must be None, a type "
                        f"or a tuple - not {types}.", exc_type=TypeError)

        if types is not None and values is not None:
            self._raise(f"'types' and 'values' were both specified for option '{name}'.")

        if types is bool:
            values = (True, False)

        if not recordable:
            self._all_recordable = False

        default_provided = default is not _UNDEFINED

        if default_provided and default is None:
            # specifying default=None implies allow_none
            allow_none = True

        self._dict[name] = {
            'val': default,
            'values': values,
            'types': types,
            'desc': desc,
            'upper': upper,
            'lower': lower,
            'check_valid': check_valid,
            'has_been_set': default_provided,
            'allow_none': allow_none,
            'recordable': recordable,
            'deprecation': deprecation,
            'need_deprecation': deprecation is not None,
        }

        # If a default is given, check for validity
        if default_provided:
            self._assert_valid(name, default)

    def undeclare(self, name):
        """
        Remove entry from the OptionsDictionary, for classes that don't use that option.

        Parameters
        ----------
        name : str
            The name of a key, the entry of which will be removed from the internal dictionary.
        """
        if name in self._dict:
            del self._dict[name]

    def update(self, in_dict):
        """
        Update the internal dictionary with the given one.

        Parameters
        ----------
        in_dict : dict
            The incoming dictionary to add to the internal one.
        """
        for name in in_dict:
            self[name] = in_dict[name]

    def __iter__(self):
        """
        Provide an iterator.

        Returns
        -------
        iterable
            iterator over the keys in the dictionary.
        """
        return iter(self._dict)

    def __contains__(self, key):
        """
        Check if the key is in the local dictionary.

        Parameters
        ----------
        key : str
            name of the option.

        Returns
        -------
        bool
            whether key is in the local dict.
        """
        return key in self._dict

    def __setitem__(self, name, value):
        """
        Set an option in the local dictionary.

        Parameters
        ----------
        name : str
            name of the option.
        value : -
            value of the option to be value- and type-checked if declared.
        """
        try:
            meta = self._dict[name]
        except KeyError:
            # The key must have been declared.
            self._raise(f"Option '{name}' cannot be set because it has not been declared.",
                        exc_type=KeyError)

        if meta['need_deprecation']:
            warn_deprecation(meta['deprecation'])
            meta['need_deprecation'] = False

        if self._read_only:
            self._raise(f"Tried to set read-only option '{name}'.", exc_type=KeyError)

        self._assert_valid(name, value)

        meta['val'] = value
        meta['has_been_set'] = True

    def __getitem__(self, name):
        """
        Get an option from the dict or declared default.

        Parameters
        ----------
        name : str
            name of the option.

        Returns
        -------
        value : -
            value of the option.
        """
        # If the option has been set in this system, return the set value
        try:
            meta = self._dict[name]
        except KeyError:
            self._raise(f"Option '{name}' cannot be found", exc_type=KeyError)

        if meta['need_deprecation']:
            warn_deprecation(meta['deprecation'])
            meta['need_deprecation'] = False
        if meta['has_been_set']:
            return meta['val']
        else:
            self._raise(f"Option '{name}' is required but has not been set.")

    def get(self, name, default=None):
        """
        Get an option from the dict or the specified default if it doesn't exist in the dict.

        Parameters
        ----------
        name : str
            name of the option.
        default : object
            Value returned if the named option doesn't exist in the option dict.

        Returns
        -------
        value : -
            value of the option, or the default if the option doesn't exist.
        """
        if name in self._dict:
            return self._dict[name]['val']
        return default

    def items(self):
        """
        Yield name and value of options.

        Yields
        ------
        key : str
            Name of option.
        value : int or bool or float or string
            Value of the option.
        """
        for key, val in self._dict.items():
            try:
                yield key, val['val']
            except KeyError:
                yield key, val['value']
