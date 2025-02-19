# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
# pylint: disable=too-many-lines
"""
The QueryBuilder: A class that allows you to query the AiiDA database, independent from backend.
Note that the backend implementation is enforced and handled with a composition model!
:func:`QueryBuilder` is the frontend class that the user can use. It inherits from *object* and contains
backend-specific functionality. Backend specific functionality is provided by the implementation classes.

These inherit from :func:`aiida.orm.implementation.BackendQueryBuilder`,
an interface classes which enforces the implementation of its defined methods.
An instance of one of the implementation classes becomes a member of the :func:`QueryBuilder` instance
when instantiated by the user.
"""
from inspect import isclass as inspect_isclass
import copy
import logging
import warnings

from sqlalchemy import and_, or_, not_, func as sa_func, select, join
from sqlalchemy.types import Integer
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import cast as type_cast
from sqlalchemy.dialects.postgresql import array

from aiida.common.links import LinkType
from aiida.manage.manager import get_manager
from aiida.common.exceptions import ConfigurationError

from . import authinfos
from . import comments
from . import computers
from . import groups
from . import logs
from . import users
from . import entities
from . import convert

__all__ = ('QueryBuilder',)

_LOGGER = logging.getLogger(__name__)

# This global variable is necessary to enable the subclassing functionality for the `Group` entity. The current
# implementation of the `QueryBuilder` was written with the assumption that only `Node` was subclassable. Support for
# subclassing was added later for `Group` and is based on its `type_string`, but the current implementation does not
# allow to extend this support to the `QueryBuilder` in an elegant way. The prefix `group.` needs to be used in various
# places to make it work, but really the internals of the `QueryBuilder` should be rewritten to in principle support
# subclassing for any entity type. This workaround should then be able to be removed.
GROUP_ENTITY_TYPE_PREFIX = 'group.'


def get_querybuilder_classifiers_from_cls(cls, query):  # pylint: disable=invalid-name
    """
    Return the correct classifiers for the QueryBuilder from an ORM class.

    :param cls: an AiiDA ORM class or backend ORM class.
    :param query: an instance of the appropriate QueryBuilder backend.
    :returns: the ORM class as well as a dictionary with additional classifier strings
    :rtype: cls, dict

    Note: the ormclass_type_string is currently hardcoded for group, computer etc. One could instead use something like
        aiida.orm.utils.node.get_type_string_from_class(cls.__module__, cls.__name__)
    """
    # pylint: disable=protected-access,too-many-branches,too-many-statements
    # Note: Unable to move this import to the top of the module for some reason
    from aiida.engine import Process
    from aiida.orm.utils.node import is_valid_node_type_string

    classifiers = {}

    classifiers['process_type_string'] = None

    # Nodes
    if issubclass(cls, query.Node):
        # If a backend ORM node (i.e. DbNode) is passed.
        # Users shouldn't do that, by why not...
        classifiers['ormclass_type_string'] = query.AiidaNode._plugin_type_string
        ormclass = cls

    elif issubclass(cls, query.AiidaNode):
        classifiers['ormclass_type_string'] = cls._plugin_type_string
        ormclass = query.Node

    # Groups:
    elif issubclass(cls, query.Group):
        classifiers['ormclass_type_string'] = GROUP_ENTITY_TYPE_PREFIX + cls._type_string
        ormclass = cls
    elif issubclass(cls, groups.Group):
        classifiers['ormclass_type_string'] = GROUP_ENTITY_TYPE_PREFIX + cls._type_string
        ormclass = query.Group

    # Computers:
    elif issubclass(cls, query.Computer):
        classifiers['ormclass_type_string'] = 'computer'
        ormclass = cls
    elif issubclass(cls, computers.Computer):
        classifiers['ormclass_type_string'] = 'computer'
        ormclass = query.Computer

    # Users
    elif issubclass(cls, query.User):
        classifiers['ormclass_type_string'] = 'user'
        ormclass = cls
    elif issubclass(cls, users.User):
        classifiers['ormclass_type_string'] = 'user'
        ormclass = query.User

    # AuthInfo
    elif issubclass(cls, query.AuthInfo):
        classifiers['ormclass_type_string'] = 'authinfo'
        ormclass = cls
    elif issubclass(cls, authinfos.AuthInfo):
        classifiers['ormclass_type_string'] = 'authinfo'
        ormclass = query.AuthInfo

    # Comment
    elif issubclass(cls, query.Comment):
        classifiers['ormclass_type_string'] = 'comment'
        ormclass = cls
    elif issubclass(cls, comments.Comment):
        classifiers['ormclass_type_string'] = 'comment'
        ormclass = query.Comment

    # Log
    elif issubclass(cls, query.Log):
        classifiers['ormclass_type_string'] = 'log'
        ormclass = cls
    elif issubclass(cls, logs.Log):
        classifiers['ormclass_type_string'] = 'log'
        ormclass = query.Log

    # Process
    # This is a special case, since Process is not an ORM class.
    # We need to deduce the ORM class used by the Process.
    elif issubclass(cls, Process):
        classifiers['ormclass_type_string'] = cls._node_class._plugin_type_string
        classifiers['process_type_string'] = cls.build_process_type()
        ormclass = query.Node

    else:
        raise ValueError(f'I do not know what to do with {cls}')

    if ormclass == query.Node:
        is_valid_node_type_string(classifiers['ormclass_type_string'], raise_on_false=True)

    return ormclass, classifiers


def get_querybuilder_classifiers_from_type(ormclass_type_string, query):  # pylint: disable=invalid-name
    """
    Return the correct classifiers for the QueryBuilder from an ORM type string.

    :param ormclass_type_string: type string for ORM class
    :param query: an instance of the appropriate QueryBuilder backend.
    :returns: the ORM class as well as a dictionary with additional classifier strings
    :rtype: cls, dict


    Same as get_querybuilder_classifiers_from_cls, but accepts a string instead of a class.
    """
    from aiida.orm.utils.node import is_valid_node_type_string
    classifiers = {}

    classifiers['process_type_string'] = None
    classifiers['ormclass_type_string'] = ormclass_type_string.lower()

    if classifiers['ormclass_type_string'].startswith(GROUP_ENTITY_TYPE_PREFIX):
        classifiers['ormclass_type_string'] = 'group.core'
        ormclass = query.Group
    elif classifiers['ormclass_type_string'] == 'computer':
        ormclass = query.Computer
    elif classifiers['ormclass_type_string'] == 'user':
        ormclass = query.User
    else:
        # At this point, we assume it is a node. The only valid type string then is a string
        # that matches exactly the _plugin_type_string of a node class
        classifiers['ormclass_type_string'] = ormclass_type_string  # no lowercase
        ormclass = query.Node

    if ormclass == query.Node:
        is_valid_node_type_string(classifiers['ormclass_type_string'], raise_on_false=True)

    return ormclass, classifiers


def get_node_type_filter(classifiers, subclassing):
    """
    Return filter dictionaries given a set of classifiers.

    :param classifiers: a dictionary with classifiers (note: does *not* support lists)
    :param subclassing: if True, allow for subclasses of the ormclass

    :returns: dictionary in QueryBuilder filter language to pass into {"type": ... }
    :rtype: dict

    """
    from aiida.orm.utils.node import get_query_type_from_type_string
    from aiida.common.escaping import escape_for_sql_like
    value = classifiers['ormclass_type_string']

    if not subclassing:
        filters = {'==': value}
    else:
        # Note: the query_type_string always ends with a dot. This ensures that "like {str}%" matches *only*
        # the query type string
        filters = {'like': f'{escape_for_sql_like(get_query_type_from_type_string(value))}%'}

    return filters


def get_process_type_filter(classifiers, subclassing):
    """
    Return filter dictionaries given a set of classifiers.

    :param classifiers: a dictionary with classifiers (note: does *not* support lists)
    :param subclassing: if True, allow for subclasses of the process type
            This is activated only, if an entry point can be found for the process type
            (as well as for a selection of built-in process types)


    :returns: dictionary in QueryBuilder filter language to pass into {"process_type": ... }
    :rtype: dict

    """
    from aiida.common.escaping import escape_for_sql_like
    from aiida.common.warnings import AiidaEntryPointWarning
    from aiida.engine.processes.process import get_query_string_from_process_type_string

    value = classifiers['process_type_string']

    if not subclassing:
        filters = {'==': value}
    else:
        if ':' in value:
            # if value is an entry point, do usual subclassing

            # Note: the process_type_string stored in the database does *not* end in a dot.
            # In order to avoid that querying for class 'Begin' will also find class 'BeginEnd',
            # we need to search separately for equality and 'like'.
            filters = {
                'or': [
                    {
                        '==': value
                    },
                    {
                        'like': escape_for_sql_like(get_query_string_from_process_type_string(value))
                    },
                ]
            }
        elif value.startswith('aiida.engine'):
            # For core process types, a filter is not is needed since each process type has a corresponding
            # ormclass type that already specifies everything.
            # Note: This solution is fragile and will break as soon as there is not an exact one-to-one correspondence
            # between process classes and node classes

            # Note: Improve this when issue #2475 is addressed
            filters = {'like': '%'}
        else:
            warnings.warn(
                "Process type '{value}' does not correspond to a registered entry. "
                'This risks queries to fail once the location of the process class changes. '
                "Add an entry point for '{value}' to remove this warning.".format(value=value), AiidaEntryPointWarning
            )
            filters = {
                'or': [
                    {
                        '==': value
                    },
                    {
                        'like': escape_for_sql_like(get_query_string_from_process_type_string(value))
                    },
                ]
            }

    return filters


def get_group_type_filter(classifiers, subclassing):
    """Return filter dictionaries for `Group.type_string` given a set of classifiers.

    :param classifiers: a dictionary with classifiers (note: does *not* support lists)
    :param subclassing: if True, allow for subclasses of the ormclass

    :returns: dictionary in QueryBuilder filter language to pass into {'type_string': ... }
    :rtype: dict
    """
    from aiida.common.escaping import escape_for_sql_like

    value = classifiers['ormclass_type_string'][len(GROUP_ENTITY_TYPE_PREFIX):]

    if not subclassing:
        filters = {'==': value}
    else:
        # This is a hardcoded solution to the problem that the base class `Group` should match all subclasses, however
        # its entry point string is `core` and so will only match those subclasses whose entry point also starts with
        # 'core', however, this is only the case for group subclasses shipped with `aiida-core`. Any plugins from
        # external packages will never be matched. Making the entry point name of `Group` an empty string is also not
        # possible so we perform the switch here in code.
        if value == 'core':
            value = ''
        filters = {'like': f'{escape_for_sql_like(value)}%'}

    return filters


class QueryBuilder:
    """
    The class to query the AiiDA database.

    Usage::

        from aiida.orm.querybuilder import QueryBuilder
        qb = QueryBuilder()
        # Querying nodes:
        qb.append(Node)
        # retrieving the results:
        results = qb.all()

    """

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    # This tag defines how edges are tagged (labeled) by the QueryBuilder default
    # namely tag of first entity + _EDGE_TAG_DELIM + tag of second entity
    _EDGE_TAG_DELIM = '--'
    _VALID_PROJECTION_KEYS = ('func', 'cast')

    def __init__(self, backend=None, **kwargs):
        """
        Instantiates a QueryBuilder instance.

        Which backend is used decided here based on backend-settings (taken from the user profile).
        This cannot be overriden so far by the user.

        :param bool debug:
            Turn on debug mode. This feature prints information on the screen about the stages
            of the QueryBuilder. Does not affect results.
        :param list path:
            A list of the vertices to traverse. Leave empty if you plan on using the method
            :func:`QueryBuilder.append`.
        :param filters:
            The filters to apply. You can specify the filters here, when appending to the query
            using :func:`QueryBuilder.append` or even later using :func:`QueryBuilder.add_filter`.
            Check latter gives API-details.
        :param project:
            The projections to apply. You can specify the projections here, when appending to the query
            using :func:`QueryBuilder.append` or even later using :func:`QueryBuilder.add_projection`.
            Latter gives you API-details.
        :param int limit:
            Limit the number of rows to this number. Check :func:`QueryBuilder.limit`
            for more information.
        :param int offset:
            Set an offset for the results returned. Details in :func:`QueryBuilder.offset`.
        :param order_by:
            How to order the results. As the 2 above, can be set also at later stage,
            check :func:`QueryBuilder.order_by` for more information.

        """
        backend = backend or get_manager().get_backend()
        self._impl = backend.query()

        # A list storing the path being traversed by the query
        self._path = []

        # A list of unique aliases in same order as path
        self._aliased_path = []

        # A dictionary tag:alias of ormclass
        # redundant but makes life easier
        self.tag_to_alias_map = {}
        self.tag_to_projected_property_dict = {}

        # A dictionary tag: filter specification for this alias
        self._filters = {}

        # A dictionary tag: projections for this alias
        self._projections = {}
        self.nr_of_projections = 0

        self._attrkeys_as_in_sql_result = None

        self._query = None

        # A dictionary for classes passed to the tag given to them
        # Everything is specified with unique tags, which are strings.
        # But somebody might not care about giving tags, so to do
        # everything with classes one needs a map, that also defines classes
        # as tags, to allow the following example:

        # qb = QueryBuilder()
        # qb.append(PwCalculation)
        # qb.append(StructureData, with_outgoing=PwCalculation)

        # The cls_to_tag_map in this case would be:
        # {PwCalculation:'PwCalculation', StructureData:'StructureData'}
        # Keep in mind that it needs to be checked (and this is done) whether the class
        # is used twice. In that case, the user has to provide a tag!
        self._cls_to_tag_map = {}

        # Hashing the the internal queryhelp allows me to avoid to build a query again
        self._hash = None

        # The hash being None implies that the query will be build (Check the code in .get_query
        # The user can inject a query, this keyword stores whether this was done.
        # Check QueryBuilder.inject_query
        self._injected = False

        # Setting debug levels:
        self.set_debug(kwargs.pop('debug', False))

        # One can apply the path as a keyword. Allows for jsons to be given to the QueryBuilder.
        path = kwargs.pop('path', [])
        if not isinstance(path, (tuple, list)):
            raise TypeError('Path needs to be a tuple or a list')
        # If the user specified a path, I use the append method to analyze, see QueryBuilder.append
        for path_spec in path:
            if isinstance(path_spec, dict):
                self.append(**path_spec)
            elif isinstance(path_spec, str):
                # Maybe it is just a string,
                # I assume user means the type
                self.append(entity_type=path_spec)
            else:
                # Or a class, let's try
                self.append(cls=path_spec)

        # Projections. The user provides a dictionary, but the specific checks is
        # left to QueryBuilder.add_project.
        projection_dict = kwargs.pop('project', {})
        if not isinstance(projection_dict, dict):
            raise TypeError('You need to provide the projections as dictionary')
        for key, val in projection_dict.items():
            self.add_projection(key, val)

        # For filters, I also expect a dictionary, and the checks are done lower.
        filter_dict = kwargs.pop('filters', {})
        if not isinstance(filter_dict, dict):
            raise TypeError('You need to provide the filters as dictionary')
        for key, val in filter_dict.items():
            self.add_filter(key, val)

        # The limit is caps the number of results returned, and can also be set with QueryBuilder.limit
        self.limit(kwargs.pop('limit', None))

        # The offset returns results after the offset
        self.offset(kwargs.pop('offset', None))

        # The user can also specify the order.
        self._order_by = {}
        order_spec = kwargs.pop('order_by', None)
        if order_spec:
            self.order_by(order_spec)

        # I've gone through all the keywords, popping each item
        # If kwargs is not empty, there is a problem:
        if kwargs:
            valid_keys = ('path', 'filters', 'project', 'limit', 'offset', 'order_by')
            raise ValueError(
                'Received additional keywords: {}'
                '\nwhich I cannot process'
                '\nValid keywords are: {}'
                ''.format(list(kwargs.keys()), valid_keys)
            )

    def __str__(self):
        """
        When somebody hits: print(QueryBuilder) or print(str(QueryBuilder))
        I want to print the SQL-query. Because it looks cool...
        """
        from aiida.manage.configuration import get_config

        config = get_config()
        engine = config.current_profile.database_engine

        if engine.startswith('mysql'):
            from sqlalchemy.dialects import mysql as mydialect
        elif engine.startswith('postgre'):
            from sqlalchemy.dialects import postgresql as mydialect
        else:
            raise ConfigurationError(f'Unknown DB engine: {engine}')

        que = self.get_query()
        return str(que.statement.compile(compile_kwargs={'literal_binds': True}, dialect=mydialect.dialect()))

    def _get_ormclass(self, cls, ormclass_type_string):
        """
        Get ORM classifiers from either class(es) or ormclass_type_string(s).

        :param cls: a class or tuple/set/list of classes that are either AiiDA ORM classes or backend ORM classes.
        :param ormclass_type_string: type string for ORM class

        :returns: the ORM class as well as a dictionary with additional classifier strings

        Handles the case of lists as well.
        """
        if cls is not None:
            func = get_querybuilder_classifiers_from_cls
            input_info = cls
        elif ormclass_type_string is not None:
            func = get_querybuilder_classifiers_from_type
            input_info = ormclass_type_string
        else:
            raise RuntimeError('Neither cls nor ormclass_type_string specified')

        if isinstance(input_info, (tuple, list, set)):
            # Going through each element of the list/tuple/set:
            ormclass = None
            classifiers = []

            for index, classifier in enumerate(input_info):
                new_ormclass, new_classifiers = func(classifier, self._impl)
                if index:
                    # This is not my first iteration!
                    # I check consistency with what was specified before
                    if new_ormclass != ormclass:
                        raise ValueError('Non-matching types have been passed as list/tuple/set.')
                else:
                    # first iteration
                    ormclass = new_ormclass

                classifiers.append(new_classifiers)
        else:
            ormclass, classifiers = func(input_info, self._impl)

        return ormclass, classifiers

    def _get_unique_tag(self, classifiers):
        """
        Using the function get_tag_from_type, I get a tag.
        I increment an index that is appended to that tag until I have an unused tag.
        This function is called in :func:`QueryBuilder.append` when autotag is set to True.

        :param dict classifiers:
            Classifiers, containing the string that defines the type of the AiiDA ORM class.
            For subclasses of Node, this is the Node._plugin_type_string, for other they are
            as defined as returned by :func:`QueryBuilder._get_ormclass`.

            Can also be a list of dictionaries, when multiple classes are passed to QueryBuilder.append

        :returns: A tag as a string (it is a single string also when passing multiple classes).
        """

        def get_tag_from_type(classifiers):
            """
            Assign a tag to the given vertex of a path, based mainly on the type
            *   data.structure.StructureData -> StructureData
            *   data.structure.StructureData. -> StructureData
            *   calculation.job.quantumespresso.pw.PwCalculation. -. PwCalculation
            *   node.Node. -> Node
            *   Node -> Node
            *   computer -> computer
            *   etc.

            :param str ormclass_type_string:
                The string that defines the type of the AiiDA ORM class.
                For subclasses of Node, this is the Node._plugin_type_string, for other they are
                as defined as returned by :func:`QueryBuilder._get_ormclass`.
            :returns: A tag, as a string.
            """
            if isinstance(classifiers, list):
                return '-'.join([t['ormclass_type_string'].rstrip('.').split('.')[-1] or 'node' for t in classifiers])

            return classifiers['ormclass_type_string'].rstrip('.').split('.')[-1] or 'node'

        basetag = get_tag_from_type(classifiers)
        tags_used = self.tag_to_alias_map.keys()
        for i in range(1, 100):
            tag = f'{basetag}_{i}'
            if tag not in tags_used:
                return tag

        raise RuntimeError('Cannot find a tag after 100 tries')

    def append(
        self,
        cls=None,
        entity_type=None,
        tag=None,
        filters=None,
        project=None,
        subclassing=True,
        edge_tag=None,
        edge_filters=None,
        edge_project=None,
        outerjoin=False,
        **kwargs
    ):
        """
        Any iterative procedure to build the path for a graph query
        needs to invoke this method to append to the path.

        :param cls:
            The Aiida-class (or backend-class) defining the appended vertice.
            Also supports a tuple/list of classes. This results in an all instances of
            this class being accepted in a query. However the classes have to have the same orm-class
            for the joining to work. I.e. both have to subclasses of Node. Valid is::

                cls=(StructureData, Dict)

            This is invalid:

                cls=(Group, Node)

        :param entity_type: The node type of the class, if cls is not given. Also here, a tuple or list is accepted.
        :type type: str
        :param bool autotag: Whether to find automatically a unique tag. If this is set to True (default False),
        :param str tag:
            A unique tag. If none is given, I will create a unique tag myself.
        :param filters:
            Filters to apply for this vertex.
            See :meth:`.add_filter`, the method invoked in the background, or usage examples for details.
        :param project:
            Projections to apply. See usage examples for details.
            More information also in :meth:`.add_projection`.
        :param bool subclassing:
            Whether to include subclasses of the given class
            (default **True**).
            E.g. Specifying a  ProcessNode as cls will include CalcJobNode, WorkChainNode, CalcFunctionNode, etc..
        :param bool outerjoin:
            If True, (default is False), will do a left outerjoin
            instead of an inner join
        :param str edge_tag:
            The tag that the edge will get. If nothing is specified
            (and there is a meaningful edge) the default is tag1--tag2 with tag1 being the entity joining
            from and tag2 being the entity joining to (this entity).
        :param str edge_filters:
            The filters to apply on the edge. Also here, details in :meth:`.add_filter`.
        :param str edge_project:
            The project from the edges. API-details in :meth:`.add_projection`.

        A small usage example how this can be invoked::

            qb = QueryBuilder()             # Instantiating empty querybuilder instance
            qb.append(cls=StructureData)    # First item is StructureData node
            # The
            # next node in the path is a PwCalculation, with
            # the structure joined as an input
            qb.append(
                cls=PwCalculation,
                with_incoming=StructureData
            )

        :return: self
        :rtype: :class:`aiida.orm.QueryBuilder`
        """
        # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements
        # INPUT CHECKS ##########################
        # This function can be called by users, so I am checking the
        # input now.
        # First of all, let's make sure the specified
        # the class or the type (not both)

        if cls is not None and entity_type is not None:
            raise ValueError(f'You cannot specify both a class ({cls}) and a entity_type ({entity_type})')

        if cls is None and entity_type is None:
            raise ValueError('You need to specify at least a class or a entity_type')

        # Let's check if it is a valid class or type
        if cls:
            if isinstance(cls, (tuple, list, set)):
                for sub_cls in cls:
                    if not inspect_isclass(sub_cls):
                        raise TypeError(f"{sub_cls} was passed with kw 'cls', but is not a class")
            else:
                if not inspect_isclass(cls):
                    raise TypeError(f"{cls} was passed with kw 'cls', but is not a class")
        elif entity_type is not None:
            if isinstance(entity_type, (tuple, list, set)):
                for sub_type in entity_type:
                    if not isinstance(sub_type, str):
                        raise TypeError(f'{sub_type} was passed as entity_type, but is not a string')
            else:
                if not isinstance(entity_type, str):
                    raise TypeError(f'{entity_type} was passed as entity_type, but is not a string')

        ormclass, classifiers = self._get_ormclass(cls, entity_type)

        # TAG #################################
        # Let's get a tag
        if tag:
            if self._EDGE_TAG_DELIM in tag:
                raise ValueError(
                    f'tag cannot contain {self._EDGE_TAG_DELIM}\nsince this is used as a delimiter for links'
                )
            if tag in self.tag_to_alias_map.keys():
                raise ValueError(f'This tag ({tag}) is already in use')
        else:
            tag = self._get_unique_tag(classifiers)

        # Checks complete
        # This is where I start doing changes to self!
        # Now, several things can go wrong along the way, so I need to split into
        # atomic blocks that I can reverse if something goes wrong.
        # TAG MAPPING #################################

        # Let's fill the cls_to_tag_map so that one can specify
        # this vertice in a joining specification later
        # First this only makes sense if a class was specified:

        l_class_added_to_map = False
        if cls:
            # Note: tuples can be used as array keys, lists & sets can't
            if isinstance(cls, (list, set)):
                tag_key = tuple(cls)
            else:
                tag_key = cls

            if tag_key in self._cls_to_tag_map.keys():
                # In this case, this class already stands for another
                # tag that was used before.
                # This means that the first tag will be the correct
                # one. This is dangerous and maybe should be avoided in
                # the future
                pass

            else:
                self._cls_to_tag_map[tag_key] = tag
                l_class_added_to_map = True

        # ALIASING ##############################
        try:
            self.tag_to_alias_map[tag] = aliased(ormclass)
        except Exception as exception:
            if self._debug:
                print('DEBUG: Exception caught in append, cleaning up')
                print('  ', exception)
            if l_class_added_to_map:
                self._cls_to_tag_map.pop(cls)
            self.tag_to_alias_map.pop(tag, None)
            raise

        # FILTERS ######################################
        try:
            self._filters[tag] = {}
            # Subclassing is currently only implemented for the `Node` and `Group` classes. So for those cases we need
            # to construct the correct filters corresponding to the provided classes and value of `subclassing`.
            if ormclass == self._impl.Node:
                self._add_node_type_filter(tag, classifiers, subclassing)
                self._add_process_type_filter(tag, classifiers, subclassing)

            elif ormclass == self._impl.Group:
                self._add_group_type_filter(tag, classifiers, subclassing)

            # The order has to be first _add_node_type_filter and then add_filter.
            # If the user adds a query on the type column, it overwrites what I did
            # if the user specified a filter, add it:
            if filters is not None:
                self.add_filter(tag, filters)
        except Exception as exception:
            if self._debug:
                print('DEBUG: Exception caught in append (part filters), cleaning up')
                print('  ', exception)
            if l_class_added_to_map:
                self._cls_to_tag_map.pop(cls)
            self.tag_to_alias_map.pop(tag)
            self._filters.pop(tag)
            raise

        # PROJECTIONS ##############################
        try:
            self._projections[tag] = []
            if project is not None:
                self.add_projection(tag, project)
        except Exception as exception:
            if self._debug:
                print('DEBUG: Exception caught in append (part projections), cleaning up')
                print('  ', exception)
            if l_class_added_to_map:
                self._cls_to_tag_map.pop(cls)
            self.tag_to_alias_map.pop(tag, None)
            self._filters.pop(tag)
            self._projections.pop(tag)
            raise exception

        # JOINING #####################################
        # pylint: disable=too-many-nested-blocks
        try:
            # Get the functions that are implemented:
            spec_to_function_map = []
            for secondary_dict in self._get_function_map().values():
                for key in secondary_dict.keys():
                    if key not in spec_to_function_map:
                        spec_to_function_map.append(key)
            joining_keyword = kwargs.pop('joining_keyword', None)
            joining_value = kwargs.pop('joining_value', None)
            for key, val in kwargs.items():
                if key not in spec_to_function_map:
                    raise ValueError(
                        '{} is not a valid keyword '
                        'for joining specification\n'
                        'Valid keywords are: '
                        '{}'.format(
                            key, spec_to_function_map + ['cls', 'type', 'tag', 'autotag', 'filters', 'project']
                        )
                    )
                elif joining_keyword:
                    raise ValueError(
                        'You already specified joining specification {}\n'
                        'But you now also want to specify {}'
                        ''.format(joining_keyword, key)
                    )
                else:
                    joining_keyword = key
                    if joining_keyword == 'direction':
                        if not isinstance(val, int):
                            raise TypeError('direction=n expects n to be an integer')
                        try:
                            if val < 0:
                                joining_keyword = 'with_outgoing'
                            elif val > 0:
                                joining_keyword = 'with_incoming'
                            else:
                                raise ValueError('direction=0 is not valid')
                            joining_value = self._path[-abs(val)]['tag']
                        except IndexError as exc:
                            raise ValueError(
                                f'You have specified a non-existent entity with\ndirection={joining_value}\n{exc}\n'
                            )
                    else:
                        joining_value = self._get_tag_from_specification(val)
            # the default is that this vertice is 'with_incoming' as the previous one
            if joining_keyword is None and len(self._path) > 0:
                joining_keyword = 'with_incoming'
                joining_value = self._path[-1]['tag']

        except Exception as exception:
            if self._debug:
                print('DEBUG: Exception caught in append (part joining), cleaning up')
                print('  ', exception)
            if l_class_added_to_map:
                self._cls_to_tag_map.pop(cls)
            self.tag_to_alias_map.pop(tag, None)
            self._filters.pop(tag)
            self._projections.pop(tag)
            # There's not more to clean up here!
            raise exception

        # EDGES #################################
        if len(self._path) > 0:
            try:
                if self._debug:
                    print('DEBUG: Choosing an edge_tag')
                if edge_tag is None:
                    edge_destination_tag = self._get_tag_from_specification(joining_value)
                    edge_tag = edge_destination_tag + self._EDGE_TAG_DELIM + tag
                else:
                    if edge_tag in self.tag_to_alias_map.keys():
                        raise ValueError(f'The tag {edge_tag} is already in use')
                if self._debug:
                    print('I have chosen', edge_tag)

                # My edge is None for now, since this is created on the FLY,
                # the _tag_to_alias_map will be updated later (in _build)
                self.tag_to_alias_map[edge_tag] = None

                # Filters on links:
                # Beware, I alway add this entry now, but filtering here might be
                # non-sensical, since this ONLY works for m2m relationship where
                # I go through a different table
                self._filters[edge_tag] = {}
                if edge_filters is not None:
                    self.add_filter(edge_tag, edge_filters)
                # Projections on links
                self._projections[edge_tag] = []
                if edge_project is not None:
                    self.add_projection(edge_tag, edge_project)
            except Exception as exception:

                if self._debug:
                    print('DEBUG: Exception caught in append (part joining), cleaning up')
                    import traceback
                    print(traceback.format_exc())
                if l_class_added_to_map:
                    self._cls_to_tag_map.pop(cls)
                self.tag_to_alias_map.pop(tag, None)
                self._filters.pop(tag)
                self._projections.pop(tag)
                if edge_tag is not None:
                    self.tag_to_alias_map.pop(edge_tag, None)
                    self._filters.pop(edge_tag, None)
                    self._projections.pop(edge_tag, None)
                # There's not more to clean up here!
                raise exception

        # EXTENDING THE PATH #################################
        # Note: 'type' being a list is a relict of an earlier implementation
        # Could simply pass all classifiers here.
        if isinstance(classifiers, list):
            path_type = [c['ormclass_type_string'] for c in classifiers]
        else:
            path_type = classifiers['ormclass_type_string']

        self._path.append(
            dict(
                entity_type=path_type,
                tag=tag,
                joining_keyword=joining_keyword,
                joining_value=joining_value,
                outerjoin=outerjoin,
                edge_tag=edge_tag
            )
        )

        return self

    def order_by(self, order_by):
        """
        Set the entity to order by

        :param order_by:
            This is a list of items, where each item is a dictionary specifies
            what to sort for an entity

        In each dictionary in that list, keys represent valid tags of
        entities (tables), and values are list of columns.

        Usage::

            #Sorting by id (ascending):
            qb = QueryBuilder()
            qb.append(Node, tag='node')
            qb.order_by({'node':['id']})

            # or
            #Sorting by id (ascending):
            qb = QueryBuilder()
            qb.append(Node, tag='node')
            qb.order_by({'node':[{'id':{'order':'asc'}}]})

            # for descending order:
            qb = QueryBuilder()
            qb.append(Node, tag='node')
            qb.order_by({'node':[{'id':{'order':'desc'}}]})

            # or (shorter)
            qb = QueryBuilder()
            qb.append(Node, tag='node')
            qb.order_by({'node':[{'id':'desc'}]})
        """
        # pylint: disable=too-many-nested-blocks,too-many-branches
        self._order_by = []
        allowed_keys = ('cast', 'order')
        possible_orders = ('asc', 'desc')

        if not isinstance(order_by, (list, tuple)):
            order_by = [order_by]

        for order_spec in order_by:
            if not isinstance(order_spec, dict):
                raise TypeError(
                    'Invalid input for order_by statement: {}\n'
                    'I am expecting a dictionary ORMClass,'
                    '[columns to sort]'
                    ''.format(order_spec)
                )
            _order_spec = {}
            for tagspec, items_to_order_by in order_spec.items():
                if not isinstance(items_to_order_by, (tuple, list)):
                    items_to_order_by = [items_to_order_by]
                tag = self._get_tag_from_specification(tagspec)
                _order_spec[tag] = []
                for item_to_order_by in items_to_order_by:
                    if isinstance(item_to_order_by, str):
                        item_to_order_by = {item_to_order_by: {}}
                    elif isinstance(item_to_order_by, dict):
                        pass
                    else:
                        raise ValueError(
                            f'Cannot deal with input to order_by {item_to_order_by}\nof type{type(item_to_order_by)}\n'
                        )
                    for entityname, orderspec in item_to_order_by.items():
                        # if somebody specifies eg {'node':{'id':'asc'}}
                        # tranform to {'node':{'id':{'order':'asc'}}}

                        if isinstance(orderspec, str):
                            this_order_spec = {'order': orderspec}
                        elif isinstance(orderspec, dict):
                            this_order_spec = orderspec
                        else:
                            raise TypeError(
                                'I was expecting a string or a dictionary\n'
                                'You provided {} {}\n'
                                ''.format(type(orderspec), orderspec)
                            )
                        for key in this_order_spec:
                            if key not in allowed_keys:
                                raise ValueError(
                                    'The allowed keys for an order specification\n'
                                    'are {}\n'
                                    '{} is not valid\n'
                                    ''.format(', '.join(allowed_keys), key)
                                )
                        this_order_spec['order'] = this_order_spec.get('order', 'asc')
                        if this_order_spec['order'] not in possible_orders:
                            raise ValueError(
                                'You gave {} as an order parameters,\n'
                                'but it is not a valid order parameter\n'
                                'Valid orders are: {}\n'
                                ''.format(this_order_spec['order'], possible_orders)
                            )
                        item_to_order_by[entityname] = this_order_spec

                    _order_spec[tag].append(item_to_order_by)

            self._order_by.append(_order_spec)
        return self

    def add_filter(self, tagspec, filter_spec):
        """
        Adding a filter to my filters.

        :param tagspec: The tag, which has to exist already as a key in self._filters
        :param filter_spec: The specifications for the filter, has to be a dictionary

        Usage::

            qb = QueryBuilder()         # Instantiating the QueryBuilder instance
            qb.append(Node, tag='node') # Appending a Node
            #let's put some filters:
            qb.add_filter('node',{'id':{'>':12}})
            # 2 filters together:
            qb.add_filter('node',{'label':'foo', 'uuid':{'like':'ab%'}})
            # Now I am overriding the first filter I set:
            qb.add_filter('node',{'id':13})
        """
        filters = self._process_filters(filter_spec)
        tag = self._get_tag_from_specification(tagspec)
        self._filters[tag].update(filters)

    @staticmethod
    def _process_filters(filters):
        """Process filters."""
        if not isinstance(filters, dict):
            raise TypeError('Filters have to be passed as dictionaries')

        processed_filters = {}

        for key, value in filters.items():
            if isinstance(value, entities.Entity):
                # Convert to be the id of the joined entity because we can't query
                # for the object instance directly
                processed_filters[f'{key}_id'] = value.id
            else:
                processed_filters[key] = value

        return processed_filters

    def _add_node_type_filter(self, tagspec, classifiers, subclassing):
        """
        Add a filter based on node type.

        :param tagspec: The tag, which has to exist already as a key in self._filters
        :param classifiers: a dictionary with classifiers
        :param subclassing: if True, allow for subclasses of the ormclass
        """
        if isinstance(classifiers, list):
            # If a list was passed to QueryBuilder.append, this propagates to a list in the classifiers
            entity_type_filter = {'or': []}
            for classifier in classifiers:
                entity_type_filter['or'].append(get_node_type_filter(classifier, subclassing))
        else:
            entity_type_filter = get_node_type_filter(classifiers, subclassing)

        self.add_filter(tagspec, {'node_type': entity_type_filter})

    def _add_process_type_filter(self, tagspec, classifiers, subclassing):
        """
        Add a filter based on process type.

        :param tagspec: The tag, which has to exist already as a key in self._filters
        :param classifiers: a dictionary with classifiers
        :param subclassing: if True, allow for subclasses of the process type

        Note: This function handles the case when process_type_string is None.
        """
        if isinstance(classifiers, list):
            # If a list was passed to QueryBuilder.append, this propagates to a list in the classifiers
            process_type_filter = {'or': []}
            for classifier in classifiers:
                if classifier['process_type_string'] is not None:
                    process_type_filter['or'].append(get_process_type_filter(classifier, subclassing))

            if len(process_type_filter['or']) > 0:
                self.add_filter(tagspec, {'process_type': process_type_filter})

        else:
            if classifiers['process_type_string'] is not None:
                process_type_filter = get_process_type_filter(classifiers, subclassing)
                self.add_filter(tagspec, {'process_type': process_type_filter})

    def _add_group_type_filter(self, tagspec, classifiers, subclassing):
        """
        Add a filter based on group type.

        :param tagspec: The tag, which has to exist already as a key in self._filters
        :param classifiers: a dictionary with classifiers
        :param subclassing: if True, allow for subclasses of the ormclass
        """
        if isinstance(classifiers, list):
            # If a list was passed to QueryBuilder.append, this propagates to a list in the classifiers
            type_string_filter = {'or': []}
            for classifier in classifiers:
                type_string_filter['or'].append(get_group_type_filter(classifier, subclassing))
        else:
            type_string_filter = get_group_type_filter(classifiers, subclassing)

        self.add_filter(tagspec, {'type_string': type_string_filter})

    def add_projection(self, tag_spec, projection_spec):
        r"""
        Adds a projection

        :param tag_spec: A valid specification for a tag
        :param projection_spec:
            The specification for the projection.
            A projection is a list of dictionaries, with each dictionary
            containing key-value pairs where the key is database entity
            (e.g. a column / an attribute) and the value is (optional)
            additional information on how to process this database entity.

        If the given *projection_spec* is not a list, it will be expanded to
        a list.
        If the listitems are not dictionaries, but strings (No additional
        processing of the projected results desired), they will be expanded to
        dictionaries.

        Usage::

            qb = QueryBuilder()
            qb.append(StructureData, tag='struc')

            # Will project the uuid and the kinds
            qb.add_projection('struc', ['uuid', 'attributes.kinds'])

        The above example will project the uuid and the kinds-attribute of all matching structures.
        There are 2 (so far) special keys.

        The single star *\** will project the *ORM-instance*::

            qb = QueryBuilder()
            qb.append(StructureData, tag='struc')
            # Will project the ORM instance
            qb.add_projection('struc', '*')
            print type(qb.first()[0])
            # >>> aiida.orm.nodes.data.structure.StructureData

        The double star ``**`` projects all possible projections of this entity:

            QueryBuilder().append(StructureData,tag='s', project='**').limit(1).dict()[0]['s'].keys()

            # >>> 'user_id, description, ctime, label, extras, mtime, id, attributes, dbcomputer_id, type, uuid'

        Be aware that the result of ``**`` depends on the backend implementation.

        """
        tag = self._get_tag_from_specification(tag_spec)
        _projections = []
        if self._debug:
            print('DEBUG: Adding projection of', tag_spec)
            print('   projection', projection_spec)
        if not isinstance(projection_spec, (list, tuple)):
            projection_spec = [projection_spec]
        for projection in projection_spec:
            if isinstance(projection, dict):
                _thisprojection = projection
            elif isinstance(projection, str):
                _thisprojection = {projection: {}}
            else:
                raise ValueError(f'Cannot deal with projection specification {projection}\n')
            for spec in _thisprojection.values():
                if not isinstance(spec, dict):
                    raise TypeError(
                        f'\nThe value of a key-value pair in a projection\nhas to be a dictionary\nYou gave: {spec}\n'
                    )

                for key, val in spec.items():
                    if key not in self._VALID_PROJECTION_KEYS:
                        raise ValueError(f'{key} is not a valid key {self._VALID_PROJECTION_KEYS}')
                    if not isinstance(val, str):
                        raise TypeError(f'{val} has to be a string')
            _projections.append(_thisprojection)
        if self._debug:
            print('   projections have become:', _projections)
        self._projections[tag] = _projections

    def _get_projectable_entity(self, alias, column_name, attrpath, **entityspec):
        """Return projectable entity for a given alias and column name."""
        if attrpath or column_name in ('attributes', 'extras'):
            entity = self._impl.get_projectable_attribute(alias, column_name, attrpath, **entityspec)
        else:
            entity = self._impl.get_column(column_name, alias)
        return entity

    def _add_to_projections(self, alias, projectable_entity_name, cast=None, func=None):
        """
        :param alias: A instance of *sqlalchemy.orm.util.AliasedClass*, alias for an ormclass
        :type alias: :class:`sqlalchemy.orm.util.AliasedClass`
        :param projectable_entity_name:
            User specification of what to project.
            Appends to query's entities what the user wants to project
            (have returned by the query)

        """
        column_name = projectable_entity_name.split('.')[0]
        attr_key = projectable_entity_name.split('.')[1:]

        if column_name == '*':
            if func is not None:
                raise ValueError(
                    'Very sorry, but functions on the aliased class\n'
                    "(You specified '*')\n"
                    'will not work!\n'
                    "I suggest you apply functions on a column, e.g. ('id')\n"
                )
            self._query = self._query.add_entity(alias)
        else:
            entity_to_project = self._get_projectable_entity(alias, column_name, attr_key, cast=cast)
            if func is None:
                pass
            elif func == 'max':
                entity_to_project = sa_func.max(entity_to_project)
            elif func == 'min':
                entity_to_project = sa_func.max(entity_to_project)
            elif func == 'count':
                entity_to_project = sa_func.count(entity_to_project)
            else:
                raise ValueError(f'\nInvalid function specification {func}')
            self._query = self._query.add_columns(entity_to_project)

    def _build_projections(self, tag, items_to_project=None):
        """Build the projections for a given tag."""
        if items_to_project is None:
            items_to_project = self._projections.get(tag, [])

        # Return here if there is nothing to project, reduces number of key in return dictionary
        if self._debug:
            print(tag, items_to_project)
        if not items_to_project:
            return

        alias = self.tag_to_alias_map[tag]

        self.tag_to_projected_property_dict[tag] = {}

        for projectable_spec in items_to_project:
            for projectable_entity_name, extraspec in projectable_spec.items():
                property_names = list()
                if projectable_entity_name == '**':
                    # Need to expand
                    property_names.extend(self._impl.modify_expansions(alias, self._impl.get_column_names(alias)))
                else:
                    property_names.extend(self._impl.modify_expansions(alias, [projectable_entity_name]))

                for property_name in property_names:
                    self._add_to_projections(alias, property_name, **extraspec)
                    self.tag_to_projected_property_dict[tag][property_name] = self.nr_of_projections
                    self.nr_of_projections += 1

    def _get_tag_from_specification(self, specification):
        """
        :param specification: If that is a string, I assume the user has
            deliberately specified it with tag=specification.
            In that case, I simply check that it's not a duplicate.
            If it is a class, I check if it's in the _cls_to_tag_map!
        """
        if isinstance(specification, str):
            if specification in self.tag_to_alias_map.keys():
                tag = specification
            else:
                raise ValueError(
                    f'tag {specification} is not among my known tags\nMy tags are: {self.tag_to_alias_map.keys()}'
                )
        else:
            if specification in self._cls_to_tag_map.keys():
                tag = self._cls_to_tag_map[specification]
            else:
                raise ValueError(
                    'You specified as a class for which I have to find a tag\n'
                    'The classes that I can do this for are:{}\n'
                    'The tags I have are: {}'.format(self._cls_to_tag_map.keys(), self.tag_to_alias_map.keys())
                )
        return tag

    def set_debug(self, debug):
        """
        Run in debug mode. This does not affect functionality, but prints intermediate stages
        when creating a query on screen.

        :param bool debug: Turn debug on or off
        """
        if not isinstance(debug, bool):
            return TypeError('I expect a boolean')
        self._debug = debug

        return self

    def limit(self, limit):
        """
        Set the limit (nr of rows to return)

        :param int limit: integers of number of rows of rows to return
        """

        if (limit is not None) and (not isinstance(limit, int)):
            raise TypeError('The limit has to be an integer, or None')
        self._limit = limit
        return self

    def offset(self, offset):
        """
        Set the offset. If offset is set, that many rows are skipped before returning.
        *offset* = 0 is the same as omitting setting the offset.
        If both offset and limit appear,
        then *offset* rows are skipped before starting to count the *limit* rows
        that are returned.

        :param int offset: integers of nr of rows to skip
        """
        if (offset is not None) and (not isinstance(offset, int)):
            raise TypeError('offset has to be an integer, or None')
        self._offset = offset
        return self

    def _build_filters(self, alias, filter_spec):
        """
        Recurse through the filter specification and apply filter operations.

        :param alias: The alias of the ORM class the filter will be applied on
        :param filter_spec: the specification as given by the queryhelp

        :returns: an instance of *sqlalchemy.sql.elements.BinaryExpression*.
        """
        expressions = []
        for path_spec, filter_operation_dict in filter_spec.items():
            if path_spec in ('and', 'or', '~or', '~and', '!and', '!or'):
                subexpressions = [
                    self._build_filters(alias, sub_filter_spec) for sub_filter_spec in filter_operation_dict
                ]
                if path_spec == 'and':
                    expressions.append(and_(*subexpressions))
                elif path_spec == 'or':
                    expressions.append(or_(*subexpressions))
                elif path_spec in ('~and', '!and'):
                    expressions.append(not_(and_(*subexpressions)))
                elif path_spec in ('~or', '!or'):
                    expressions.append(not_(or_(*subexpressions)))
            else:
                column_name = path_spec.split('.')[0]

                attr_key = path_spec.split('.')[1:]
                is_attribute = (attr_key or column_name in ('attributes', 'extras'))
                try:
                    column = self._impl.get_column(column_name, alias)
                except (ValueError, TypeError):
                    if is_attribute:
                        column = None
                    else:
                        raise
                if not isinstance(filter_operation_dict, dict):
                    filter_operation_dict = {'==': filter_operation_dict}
                for operator, value in filter_operation_dict.items():
                    expressions.append(
                        self._impl.get_filter_expr(
                            operator,
                            value,
                            attr_key,
                            is_attribute=is_attribute,
                            column=column,
                            column_name=column_name,
                            alias=alias
                        )
                    )
        return and_(*expressions)

    @staticmethod
    def _check_dbentities(entities_cls_joined, entities_cls_to_join, relationship):
        """
        :param entities_cls_joined:
            A tuple of the aliased class passed as joined_entity and
            the ormclass that was expected
        :type entities_cls_to_join: tuple
        :param entities_cls_joined:
            A tuple of the aliased class passed as entity_to_join and
            the ormclass that was expected
        :type entities_cls_to_join: tuple
        :param str relationship:
            The relationship between the two entities to make the Exception
            comprehensible
        """
        # pylint: disable=protected-access
        for entity, cls in (entities_cls_joined, entities_cls_to_join):

            if not issubclass(entity._sa_class_manager.class_, cls):
                raise TypeError(
                    "You are attempting to join {} as '{}' of {}\n"
                    'This failed because you passed:\n'
                    ' - {} as entity joined (expected {})\n'
                    ' - {} as entity to join (expected {})\n'
                    '\n'.format(
                        entities_cls_joined[0].__name__,
                        relationship,
                        entities_cls_to_join[0].__name__,
                        entities_cls_joined[0]._sa_class_manager.class_.__name__,
                        entities_cls_joined[1].__name__,
                        entities_cls_to_join[0]._sa_class_manager.class_.__name__,
                        entities_cls_to_join[1].__name__,
                    )
                )

    def _join_outputs(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: The (aliased) ORMclass that is an input
        :param entity_to_join: The (aliased) ORMClass that is an output.

        **joined_entity** and **entity_to_join** are joined with a link
        from **joined_entity** as input to **enitity_to_join** as output
        (**enitity_to_join** is *with_incoming* **joined_entity**)
        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Node), 'with_incoming')

        aliased_edge = aliased(self._impl.Link)
        self._query = self._query.join(aliased_edge, aliased_edge.input_id == joined_entity.id,
                                       isouter=isouterjoin).join(
                                           entity_to_join,
                                           aliased_edge.output_id == entity_to_join.id,
                                           isouter=isouterjoin
                                       )
        return aliased_edge

    def _join_inputs(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: The (aliased) ORMclass that is an output
        :param entity_to_join: The (aliased) ORMClass that is an input.

        **joined_entity** and **entity_to_join** are joined with a link
        from **joined_entity** as output to **enitity_to_join** as input
        (**enitity_to_join** is *with_outgoing* **joined_entity**)

        """

        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Node), 'with_outgoing')
        aliased_edge = aliased(self._impl.Link)
        self._query = self._query.join(
            aliased_edge,
            aliased_edge.output_id == joined_entity.id,
        ).join(entity_to_join, aliased_edge.input_id == entity_to_join.id, isouter=isouterjoin)
        return aliased_edge

    def _join_descendants_recursive(self, joined_entity, entity_to_join, isouterjoin, filter_dict, expand_path=False):
        """
        joining descendants using the recursive functionality
        :TODO: Move the filters to be done inside the recursive query (for example on depth)
        :TODO: Pass an option to also show the path, if this is wanted.
        """

        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Node), 'with_ancestors')

        link1 = aliased(self._impl.Link)
        link2 = aliased(self._impl.Link)
        node1 = aliased(self._impl.Node)
        in_recursive_filters = self._build_filters(node1, filter_dict)

        selection_walk_list = [
            link1.input_id.label('ancestor_id'),
            link1.output_id.label('descendant_id'),
            type_cast(0, Integer).label('depth'),
        ]
        if expand_path:
            selection_walk_list.append(array((link1.input_id, link1.output_id)).label('path'))

        walk = select(selection_walk_list).select_from(join(node1, link1, link1.input_id == node1.id)).where(
            and_(
                in_recursive_filters,  # I apply filters for speed here
                link1.type.in_((LinkType.CREATE.value, LinkType.INPUT_CALC.value))  # I follow input and create links
            )
        ).cte(recursive=True)

        aliased_walk = aliased(walk)

        selection_union_list = [
            aliased_walk.c.ancestor_id.label('ancestor_id'),
            link2.output_id.label('descendant_id'),
            (aliased_walk.c.depth + type_cast(1, Integer)).label('current_depth')
        ]
        if expand_path:
            selection_union_list.append((aliased_walk.c.path + array((link2.output_id,))).label('path'))

        descendants_recursive = aliased(
            aliased_walk.union_all(
                select(selection_union_list).select_from(
                    join(
                        aliased_walk,
                        link2,
                        link2.input_id == aliased_walk.c.descendant_id,
                    )
                ).where(link2.type.in_((LinkType.CREATE.value, LinkType.INPUT_CALC.value)))
            )
        )  # .alias()

        self._query = self._query.join(descendants_recursive,
                                       descendants_recursive.c.ancestor_id == joined_entity.id).join(
                                           entity_to_join,
                                           descendants_recursive.c.descendant_id == entity_to_join.id,
                                           isouter=isouterjoin
                                       )
        return descendants_recursive.c

    def _join_ancestors_recursive(self, joined_entity, entity_to_join, isouterjoin, filter_dict, expand_path=False):
        """
        joining ancestors using the recursive functionality
        :TODO: Move the filters to be done inside the recursive query (for example on depth)
        :TODO: Pass an option to also show the path, if this is wanted.

        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Node), 'with_ancestors')

        link1 = aliased(self._impl.Link)
        link2 = aliased(self._impl.Link)
        node1 = aliased(self._impl.Node)
        in_recursive_filters = self._build_filters(node1, filter_dict)

        selection_walk_list = [
            link1.input_id.label('ancestor_id'),
            link1.output_id.label('descendant_id'),
            type_cast(0, Integer).label('depth'),
        ]
        if expand_path:
            selection_walk_list.append(array((link1.output_id, link1.input_id)).label('path'))

        walk = select(selection_walk_list).select_from(join(node1, link1, link1.output_id == node1.id)).where(
            and_(in_recursive_filters, link1.type.in_((LinkType.CREATE.value, LinkType.INPUT_CALC.value)))
        ).cte(recursive=True)

        aliased_walk = aliased(walk)

        selection_union_list = [
            link2.input_id.label('ancestor_id'),
            aliased_walk.c.descendant_id.label('descendant_id'),
            (aliased_walk.c.depth + type_cast(1, Integer)).label('current_depth'),
        ]
        if expand_path:
            selection_union_list.append((aliased_walk.c.path + array((link2.input_id,))).label('path'))

        ancestors_recursive = aliased(
            aliased_walk.union_all(
                select(selection_union_list).select_from(
                    join(
                        aliased_walk,
                        link2,
                        link2.output_id == aliased_walk.c.ancestor_id,
                    )
                ).where(link2.type.in_((LinkType.CREATE.value, LinkType.INPUT_CALC.value)))
                # I can't follow RETURN or CALL links
            )
        )

        self._query = self._query.join(ancestors_recursive,
                                       ancestors_recursive.c.descendant_id == joined_entity.id).join(
                                           entity_to_join,
                                           ancestors_recursive.c.ancestor_id == entity_to_join.id,
                                           isouter=isouterjoin
                                       )
        return ancestors_recursive.c

    def _join_group_members(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity:
            The (aliased) ORMclass that is
            a group in the database
        :param entity_to_join:
            The (aliased) ORMClass that is a node and member of the group

        **joined_entity** and **entity_to_join**
        are joined via the table_groups_nodes table.
        from **joined_entity** as group to **enitity_to_join** as node.
        (**enitity_to_join** is *with_group* **joined_entity**)
        """
        self._check_dbentities((joined_entity, self._impl.Group), (entity_to_join, self._impl.Node), 'with_group')
        aliased_group_nodes = aliased(self._impl.table_groups_nodes)
        self._query = self._query.join(aliased_group_nodes, aliased_group_nodes.c.dbgroup_id == joined_entity.id).join(
            entity_to_join, entity_to_join.id == aliased_group_nodes.c.dbnode_id, isouter=isouterjoin
        )
        return aliased_group_nodes

    def _join_groups(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: The (aliased) node in the database
        :param entity_to_join: The (aliased) Group

        **joined_entity** and **entity_to_join** are
        joined via the table_groups_nodes table.
        from **joined_entity** as node to **enitity_to_join** as group.
        (**enitity_to_join** is a group *with_node* **joined_entity**)
        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Group), 'with_node')
        aliased_group_nodes = aliased(self._impl.table_groups_nodes)
        self._query = self._query.join(aliased_group_nodes, aliased_group_nodes.c.dbnode_id == joined_entity.id).join(
            entity_to_join, entity_to_join.id == aliased_group_nodes.c.dbgroup_id, isouter=isouterjoin
        )
        return aliased_group_nodes

    def _join_creator_of(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: the aliased node
        :param entity_to_join: the aliased user to join to that node
        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.User), 'with_node')
        self._query = self._query.join(entity_to_join, entity_to_join.id == joined_entity.user_id, isouter=isouterjoin)

    def _join_created_by(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: the aliased user you want to join to
        :param entity_to_join: the (aliased) node or group in the DB to join with
        """
        self._check_dbentities((joined_entity, self._impl.User), (entity_to_join, self._impl.Node), 'with_user')
        self._query = self._query.join(entity_to_join, entity_to_join.user_id == joined_entity.id, isouter=isouterjoin)

    def _join_to_computer_used(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: the (aliased) computer entity
        :param entity_to_join: the (aliased) node entity

        """
        self._check_dbentities((joined_entity, self._impl.Computer), (entity_to_join, self._impl.Node), 'with_computer')
        self._query = self._query.join(
            entity_to_join, entity_to_join.dbcomputer_id == joined_entity.id, isouter=isouterjoin
        )

    def _join_computer(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An entity that can use a computer (eg a node)
        :param entity_to_join: aliased dbcomputer entity
        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Computer), 'with_node')
        self._query = self._query.join(
            entity_to_join, joined_entity.dbcomputer_id == entity_to_join.id, isouter=isouterjoin
        )

    def _join_group_user(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased dbgroup
        :param entity_to_join: aliased dbuser
        """
        self._check_dbentities((joined_entity, self._impl.Group), (entity_to_join, self._impl.User), 'with_group')
        self._query = self._query.join(entity_to_join, joined_entity.user_id == entity_to_join.id, isouter=isouterjoin)

    def _join_user_group(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased user
        :param entity_to_join: aliased group
        """
        self._check_dbentities((joined_entity, self._impl.User), (entity_to_join, self._impl.Group), 'with_user')
        self._query = self._query.join(entity_to_join, joined_entity.id == entity_to_join.user_id, isouter=isouterjoin)

    def _join_node_comment(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased node
        :param entity_to_join: aliased comment
        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Comment), 'with_node')
        self._query = self._query.join(
            entity_to_join, joined_entity.id == entity_to_join.dbnode_id, isouter=isouterjoin
        )

    def _join_comment_node(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased comment
        :param entity_to_join: aliased node
        """
        self._check_dbentities((joined_entity, self._impl.Comment), (entity_to_join, self._impl.Node), 'with_comment')
        self._query = self._query.join(
            entity_to_join, joined_entity.dbnode_id == entity_to_join.id, isouter=isouterjoin
        )

    def _join_node_log(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased node
        :param entity_to_join: aliased log
        """
        self._check_dbentities((joined_entity, self._impl.Node), (entity_to_join, self._impl.Log), 'with_node')
        self._query = self._query.join(
            entity_to_join, joined_entity.id == entity_to_join.dbnode_id, isouter=isouterjoin
        )

    def _join_log_node(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased log
        :param entity_to_join: aliased node
        """
        self._check_dbentities((joined_entity, self._impl.Log), (entity_to_join, self._impl.Node), 'with_log')
        self._query = self._query.join(
            entity_to_join, joined_entity.dbnode_id == entity_to_join.id, isouter=isouterjoin
        )

    def _join_user_comment(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased user
        :param entity_to_join: aliased comment
        """
        self._check_dbentities((joined_entity, self._impl.User), (entity_to_join, self._impl.Comment), 'with_user')
        self._query = self._query.join(entity_to_join, joined_entity.id == entity_to_join.user_id, isouter=isouterjoin)

    def _join_comment_user(self, joined_entity, entity_to_join, isouterjoin):
        """
        :param joined_entity: An aliased comment
        :param entity_to_join: aliased user
        """
        self._check_dbentities((joined_entity, self._impl.Comment), (entity_to_join, self._impl.User), 'with_comment')
        self._query = self._query.join(entity_to_join, joined_entity.user_id == entity_to_join.id, isouter=isouterjoin)

    def _get_function_map(self):
        """
        Map relationship type keywords to functions
        The new mapping (since 1.0.0a5) is a two level dictionary. The first level defines the entity which has been
        passed to the qb.append functon, and the second defines the relationship with respect to a given tag.
        """
        mapping = {
            'node': {
                'with_log': self._join_log_node,
                'with_comment': self._join_comment_node,
                'with_incoming': self._join_outputs,
                'with_outgoing': self._join_inputs,
                'with_descendants': self._join_ancestors_recursive,
                'with_ancestors': self._join_descendants_recursive,
                'with_computer': self._join_to_computer_used,
                'with_user': self._join_created_by,
                'with_group': self._join_group_members,
                'direction': None,
            },
            'computer': {
                'with_node': self._join_computer,
                'direction': None,
            },
            'user': {
                'with_comment': self._join_comment_user,
                'with_node': self._join_creator_of,
                'with_group': self._join_group_user,
                'direction': None,
            },
            'group': {
                'with_node': self._join_groups,
                'with_user': self._join_user_group,
                'direction': None,
            },
            'comment': {
                'with_user': self._join_user_comment,
                'with_node': self._join_node_comment,
                'direction': None
            },
            'log': {
                'with_node': self._join_node_log,
                'direction': None
            }
        }

        return mapping

    def _get_connecting_node(self, index, joining_keyword=None, joining_value=None, **kwargs):
        """
        :param querydict:
            A dictionary specifying how the current node
            is linked to other nodes.
        :param index: Index of this node within the path specification
        :param joining_keyword: the relation on which to join
        :param joining_value: the tag of the nodes to be joined
        """
        # pylint: disable=unused-argument
        # Set the calling entity - to allow for the correct join relation to be set
        entity_type = self._path[index]['entity_type']

        if isinstance(entity_type, str) and entity_type.startswith(GROUP_ENTITY_TYPE_PREFIX):
            calling_entity = 'group'
        elif entity_type not in ['computer', 'user', 'comment', 'log']:
            calling_entity = 'node'
        else:
            calling_entity = entity_type

        if joining_keyword == 'direction':
            if joining_value > 0:
                returnval = self._aliased_path[index - joining_value], self._join_outputs
            elif joining_value < 0:
                returnval = self._aliased_path[index + joining_value], self._join_inputs
            else:
                raise Exception('Direction 0 is not valid')
        else:
            try:
                func = self._get_function_map()[calling_entity][joining_keyword]
            except KeyError:
                raise ValueError(
                    f"'{joining_keyword}' is not a valid joining keyword for a '{calling_entity}' type entity"
                )

            if isinstance(joining_value, int):
                returnval = (self._aliased_path[joining_value], func)
            elif isinstance(joining_value, str):
                try:
                    returnval = self.tag_to_alias_map[self._get_tag_from_specification(joining_value)], func
                except KeyError:
                    raise ValueError(
                        'Key {} is unknown to the types I know about:\n'
                        '{}'.format(self._get_tag_from_specification(joining_value), self.tag_to_alias_map.keys())
                    )
        return returnval

    @property
    def queryhelp(self):
        """queryhelp dictionary correspondig to QueryBuilder instance.

        The queryhelp can be used to create a copy of the QueryBuilder instance like so::

            qb = QueryBuilder(limit=3).append(StructureData, project='id').order_by({StructureData:'id'})
            qb2 = QueryBuilder(**qb.queryhelp)

            # The following is True if no change has been made to the database.
            # Note that such a comparison can only be True if the order of results is enforced
            qb.all() == qb2.all()

        :return: a queryhelp dictionary
        """
        return copy.deepcopy({
            'path': self._path,
            'filters': self._filters,
            'project': self._projections,
            'order_by': self._order_by,
            'limit': self._limit,
            'offset': self._offset,
        })

    def __deepcopy__(self, memo):
        """Create deep copy of QueryBuilder instance."""
        return type(self)(**self.queryhelp)

    def _build_order(self, alias, entitytag, entityspec):
        """
        Build the order parameter of the query
        """
        column_name = entitytag.split('.')[0]
        attrpath = entitytag.split('.')[1:]
        if attrpath and 'cast' not in entityspec.keys():
            raise ValueError(
                'In order to project ({}), I have to cast the the values,\n'
                'but you have not specified the datatype to cast to\n'
                "You can do this with keyword 'cast'".format(entitytag)
            )

        entity = self._get_projectable_entity(alias, column_name, attrpath, **entityspec)
        order = entityspec.get('order', 'asc')
        if order == 'desc':
            entity = entity.desc()
        self._query = self._query.order_by(entity)

    def _build(self):
        """
        build the query and return a sqlalchemy.Query instance
        """
        # pylint: disable=too-many-branches

        # Starting the query by receiving a session
        # Every subclass needs to have _get_session and give me the right session
        firstalias = self.tag_to_alias_map[self._path[0]['tag']]
        self._query = self._impl.get_session().query(firstalias)

        # JOINS ################################
        for index, verticespec in enumerate(self._path[1:], start=1):
            alias = self.tag_to_alias_map[verticespec['tag']]
            # looping through the queryhelp
            # ~ if index:
            # There is nothing to join if that is the first table
            toconnectwith, connection_func = self._get_connecting_node(index, **verticespec)
            isouterjoin = verticespec.get('outerjoin')
            edge_tag = verticespec['edge_tag']

            if verticespec['joining_keyword'] in ('with_ancestors', 'with_descendants', 'ancestor_of', 'descendant_of'):
                # I treat those two cases in a special way.
                # I give them a filter_dict, to help the recursive function find a good
                # starting point. TODO: document this!
                filter_dict = self._filters.get(verticespec['joining_value'], {})
                # I also find out whether the path is used in a filter or a project
                # if so, I instruct the recursive function to build the path on the fly!
                # The default is False, cause it's super expensive
                expand_path = ((self._filters[edge_tag].get('path', None) is not None) or
                               any(['path' in d.keys() for d in self._projections[edge_tag]]))
                aliased_edge = connection_func(
                    toconnectwith, alias, isouterjoin=isouterjoin, filter_dict=filter_dict, expand_path=expand_path
                )
            else:
                aliased_edge = connection_func(toconnectwith, alias, isouterjoin=isouterjoin)
            if aliased_edge is not None:
                self.tag_to_alias_map[edge_tag] = aliased_edge

        ######################### FILTERS ##############################

        for tag, filter_specs in self._filters.items():
            try:
                alias = self.tag_to_alias_map[tag]
            except KeyError:
                raise ValueError(
                    'You looked for tag {} among the alias list\n'
                    'The tags I know are:\n{}'.format(tag, self.tag_to_alias_map.keys())
                )
            self._query = self._query.filter(self._build_filters(alias, filter_specs))

        ######################### PROJECTIONS ##########################
        # first clear the entities in the case the first item in the
        # path was not meant to be projected
        # attribute of Query instance storing entities to project:

        # Mapping between entities and the tag used/ given by user:
        self.tag_to_projected_property_dict = {}

        self.nr_of_projections = 0
        if self._debug:
            print('DEBUG:')
            print('   Printing the content of self._projections')
            print('  ', self._projections)
            print()

        if not any(self._projections.values()):
            # If user has not set projection,
            # I will simply project the last item specified!
            # Don't change, path traversal querying
            # relies on this behavior!
            self._build_projections(self._path[-1]['tag'], items_to_project=[{'*': {}}])
        else:
            for vertex in self._path:
                self._build_projections(vertex['tag'])

            # LINK-PROJECTIONS #########################

            for vertex in self._path[1:]:
                edge_tag = vertex.get('edge_tag', None)
                if self._debug:
                    print('DEBUG: Checking projections for edges:')
                    print(
                        '   This is edge {} from {}, {} of {}'.format(
                            edge_tag, vertex.get('tag'), vertex.get('joining_keyword'), vertex.get('joining_value')
                        )
                    )
                if edge_tag is not None:
                    self._build_projections(edge_tag)

        # ORDER ################################
        for order_spec in self._order_by:
            for tag, entity_list in order_spec.items():
                alias = self.tag_to_alias_map[tag]
                for entitydict in entity_list:
                    for entitytag, entityspec in entitydict.items():
                        self._build_order(alias, entitytag, entityspec)

        # LIMIT ################################
        if self._limit is not None:
            self._query = self._query.limit(self._limit)

        ######################## OFFSET ################################
        if self._offset is not None:
            self._query = self._query.offset(self._offset)

        ################ LAST BUT NOT LEAST ############################
        # pop the entity that I added to start the query
        self._query._entities.pop(0)  # pylint: disable=protected-access

        # Dirty solution coming up:
        # Sqlalchemy is by default de-duplicating results if possible.
        # This can lead to strange results, as shown in:
        # https://github.com/aiidateam/aiida-core/issues/1600
        # essentially qb.count() != len(qb.all()) in some cases.
        # We also addressed this with sqlachemy:
        # https://github.com/sqlalchemy/sqlalchemy/issues/4395#event-2002418814
        # where the following solution was sanctioned:
        self._query._has_mapper_entities = False  # pylint: disable=protected-access
        # We should monitor SQLAlchemy, for when a solution is officially supported by the API!

        # Make a list that helps the projection postprocessing
        self._attrkeys_as_in_sql_result = {
            index_in_sql_result: attrkey for tag, projected_entities_dict in self.tag_to_projected_property_dict.items()
            for attrkey, index_in_sql_result in projected_entities_dict.items()
        }

        if self.nr_of_projections > len(self._attrkeys_as_in_sql_result):
            raise ValueError('You are projecting the same key multiple times within the same node')
        ######################### DONE #################################

        return self._query

    def get_aliases(self):
        """
        :returns: the list of aliases
        """
        return self._aliased_path

    def get_alias(self, tag):
        """
        In order to continue a query by the user, this utility function
        returns the aliased ormclasses.

        :param tag: The tag for a vertice in the path
        :returns: the alias given for that vertice
        """
        tag = self._get_tag_from_specification(tag)
        return self.tag_to_alias_map[tag]

    def get_used_tags(self, vertices=True, edges=True):
        """
        Returns a list of all the vertices that are being used.
        Some parameter allow to select only subsets.
        :param bool vertices: Defaults to True. If True, adds the tags of vertices to the returned list
        :param bool edges: Defaults to True. If True,  adds the tags of edges to the returnend list.

        :returns: A list of all tags, including (if there is) also the tag give for the edges
        """

        given_tags = []
        for idx, path in enumerate(self._path):
            if vertices:
                given_tags.append(path['tag'])
            if edges and idx > 0:
                given_tags.append(path['edge_tag'])
        return given_tags

    def get_query(self):
        """
        Instantiates and manipulates a sqlalchemy.orm.Query instance if this is needed.
        First,  I check if the query instance is still valid by hashing the queryhelp.
        In this way, if a user asks for the same query twice, I am not recreating an instance.

        :returns: an instance of sqlalchemy.orm.Query that is specific to the backend used.
        """
        from aiida.common.hashing import make_hash

        # Need_to_build is True by default.
        # It describes whether the current query
        # which is an attribute _query of this instance is still valid
        # The queryhelp_hash is used to determine
        # whether the query is still valid

        queryhelp_hash = make_hash(self.queryhelp)
        # if self._hash (which is None if this function has not been invoked
        # and is a string (hash) if it has) is the same as the queryhelp
        # I can use the query again:
        # If the query was injected I never build:
        if self._hash is None:
            need_to_build = True
        elif self._injected:
            need_to_build = False
        elif self._hash == queryhelp_hash:
            need_to_build = False
        else:
            need_to_build = True

        if need_to_build:
            query = self._build()
            self._hash = queryhelp_hash
        else:
            try:
                query = self._query
            except AttributeError:
                _LOGGER.warning('AttributeError thrown even though I should have _query as an attribute')
                query = self._build()
                self._hash = queryhelp_hash
        return query

    @staticmethod
    def get_aiida_entity_res(value):
        """Convert a projected query result to front end class if it is an instance of a `BackendEntity`.

        Values that are not an `BackendEntity` instance will be returned unaltered

        :param value: a projected query result to convert
        :return: the converted value
        """
        try:
            return convert.get_orm_entity(value)
        except TypeError:
            return value

    def inject_query(self, query):
        """
        Manipulate the query an inject it back.
        This can be done to add custom filters using SQLA.
        :param query: A sqlalchemy.orm.Query instance
        """
        from sqlalchemy.orm import Query
        if not isinstance(query, Query):
            raise TypeError(f'{query} must be a subclass of {Query}')
        self._query = query
        self._injected = True

    def distinct(self):
        """
        Asks for distinct rows, which is the same as asking the backend to remove
        duplicates.
        Does not execute the query!

        If you want a distinct query::

            qb = QueryBuilder()
            # append stuff!
            qb.append(...)
            qb.append(...)
            ...
            qb.distinct().all() #or
            qb.distinct().dict()

        :returns: self
        """
        self._query = self.get_query().distinct()
        return self

    def first(self):
        """
        Executes query asking for one instance.
        Use as follows::

            qb = QueryBuilder(**queryhelp)
            qb.first()

        :returns:
            One row of results as a list
        """
        query = self.get_query()
        result = self._impl.first(query)

        if result is None:
            return None

        if not isinstance(result, (list, tuple)):
            result = [result]

        if len(result) != len(self._attrkeys_as_in_sql_result):
            raise Exception('length of query result does not match the number of specified projections')

        return [self.get_aiida_entity_res(self._impl.get_aiida_res(rowitem)) for colindex, rowitem in enumerate(result)]

    def one(self):
        """
        Executes the query asking for exactly one results. Will raise an exception if this is not the case
        :raises: MultipleObjectsError if more then one row can be returned
        :raises: NotExistent if no result was found
        """
        from aiida.common.exceptions import MultipleObjectsError, NotExistent
        self.limit(2)
        res = self.all()
        if len(res) > 1:
            raise MultipleObjectsError('More than one result was found')
        elif len(res) == 0:
            raise NotExistent('No result was found')
        return res[0]

    def count(self):
        """
        Counts the number of rows returned by the backend.

        :returns: the number of rows as an integer
        """
        query = self.get_query()
        return self._impl.count(query)

    def iterall(self, batch_size=100):
        """
        Same as :meth:`.all`, but returns a generator.
        Be aware that this is only safe if no commit will take place during this
        transaction. You might also want to read the SQLAlchemy documentation on
        https://docs.sqlalchemy.org/en/14/orm/query.html#sqlalchemy.orm.Query.yield_per


        :param int batch_size:
            The size of the batches to ask the backend to batch results in subcollections.
            You can optimize the speed of the query by tuning this parameter.

        :returns: a generator of lists
        """
        query = self.get_query()

        for item in self._impl.iterall(query, batch_size, self._attrkeys_as_in_sql_result):
            # Convert to AiiDA frontend entities (if they are such)
            for i, item_entry in enumerate(item):
                item[i] = self.get_aiida_entity_res(item_entry)

            yield item

    def iterdict(self, batch_size=100):
        """
        Same as :meth:`.dict`, but returns a generator.
        Be aware that this is only safe if no commit will take place during this
        transaction. You might also want to read the SQLAlchemy documentation on
        https://docs.sqlalchemy.org/en/14/orm/query.html#sqlalchemy.orm.Query.yield_per


        :param int batch_size:
            The size of the batches to ask the backend to batch results in subcollections.
            You can optimize the speed of the query by tuning this parameter.

        :returns: a generator of dictionaries
        """
        query = self.get_query()

        for item in self._impl.iterdict(query, batch_size, self.tag_to_projected_property_dict, self.tag_to_alias_map):
            for key, value in item.items():
                item[key] = self.get_aiida_entity_res(value)

            yield item

    def all(self, batch_size=None, flat=False):
        """Executes the full query with the order of the rows as returned by the backend.

        The order inside each row is given by the order of the vertices in the path and the order of the projections for
        each vertex in the path.

        :param int batch_size: the size of the batches to ask the backend to batch results in subcollections. You can
            optimize the speed of the query by tuning this parameter. Leave the default `None` if speed is not critical
            or if you don't know what you're doing.
        :param bool flat: return the result as a flat list of projected entities without sub lists.
        :returns: a list of lists of all projected entities.
        """
        matches = list(self.iterall(batch_size=batch_size))

        if not flat:
            return matches

        return [projection for entry in matches for projection in entry]

    def dict(self, batch_size=None):
        """
        Executes the full query with the order of the rows as returned by the backend.
        the order inside each row is given by the order of the vertices in the path
        and the order of the projections for each vertice in the path.

        :param int batch_size:
            The size of the batches to ask the backend to batch results in subcollections.
            You can optimize the speed of the query by tuning this parameter.
            Leave the default (*None*) if speed is not critical or if you don't know
            what you're doing!

        :returns:
            a list of dictionaries of all projected entities.
            Each dictionary consists of key value pairs, where the key is the tag
            of the vertice and the value a dictionary of key-value pairs where key
            is the entity description (a column name or attribute path)
            and the value the value in the DB.

        Usage::

            qb = QueryBuilder()
            qb.append(
                StructureData,
                tag='structure',
                filters={'uuid':{'==':myuuid}},
            )
            qb.append(
                Node,
                with_ancestors='structure',
                project=['entity_type', 'id'],  # returns entity_type (string) and id (string)
                tag='descendant'
            )

            # Return the dictionaries:
            print "qb.iterdict()"
            for d in qb.iterdict():
                print '>>>', d

        results in the following output::

            qb.iterdict()
            >>> {'descendant': {
                    'entity_type': 'calculation.job.quantumespresso.pw.PwCalculation.',
                    'id': 7716}
                }
            >>> {'descendant': {
                    'entity_type': 'data.remote.RemoteData.',
                    'id': 8510}
                }

        """
        return list(self.iterdict(batch_size=batch_size))

    def inputs(self, **kwargs):
        """
        Join to inputs of previous vertice in path.

        :returns: self
        """
        from aiida.orm import Node
        join_to = self._path[-1]['tag']
        cls = kwargs.pop('cls', Node)
        self.append(cls=cls, with_outgoing=join_to, autotag=True, **kwargs)
        return self

    def outputs(self, **kwargs):
        """
        Join to outputs of previous vertice in path.

        :returns: self
        """
        from aiida.orm import Node
        join_to = self._path[-1]['tag']
        cls = kwargs.pop('cls', Node)
        self.append(cls=cls, with_incoming=join_to, autotag=True, **kwargs)
        return self

    def children(self, **kwargs):
        """
        Join to children/descendants of previous vertice in path.

        :returns: self
        """
        from aiida.orm import Node
        join_to = self._path[-1]['tag']
        cls = kwargs.pop('cls', Node)
        self.append(cls=cls, with_ancestors=join_to, autotag=True, **kwargs)
        return self

    def parents(self, **kwargs):
        """
        Join to parents/ancestors of previous vertice in path.

        :returns: self
        """
        from aiida.orm import Node
        join_to = self._path[-1]['tag']
        cls = kwargs.pop('cls', Node)
        self.append(cls=cls, with_descendants=join_to, autotag=True, **kwargs)
        return self
