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
"""Tests for the migration engine (Alembic) as well as for the AiiDA migrations for SQLAlchemy."""

from contextlib import contextmanager
import os

from alembic import command
from alembic.config import Config

from aiida.backends import sqlalchemy as sa
from aiida.backends.general.migrations import utils
from aiida.backends.sqlalchemy import manager
from aiida.backends.sqlalchemy.models.base import Base
from aiida.backends.sqlalchemy.utils import flag_modified
from aiida.backends.testbase import AiidaTestCase
from .test_utils import new_database


class TestMigrationsSQLA(AiidaTestCase):
    """
    This class contains tests for the migration mechanism of SQLAlchemy called
    alembic. It checks if the migrations can be applied and removed correctly.
    """
    # The path to the folder that contains the migration configuration (the
    # actual configuration - not the testing)
    migr_method_dir_path = None
    # The path of the migration configuration (the actual configuration - not
    # the testing)
    alembic_dpath = None

    migrate_from = None
    migrate_to = None

    @classmethod
    def setUpClass(cls, *args, **kwargs):
        """
        Prepare the test class with the alembivc configuration
        """
        super().setUpClass(*args, **kwargs)
        cls.manager = manager.SqlaBackendManager()

    def setUp(self):
        """
        Go to the migrate_from revision, apply setUpBeforeMigration, then
        run the migration.
        """
        super().setUp()
        from aiida.orm import autogroup

        self.current_autogroup = autogroup.CURRENT_AUTOGROUP
        autogroup.CURRENT_AUTOGROUP = None
        assert self.migrate_from and self.migrate_to, \
            f"TestCase '{type(self).__name__}' must define migrate_from and migrate_to properties"

        try:
            self.migrate_db_down(self.migrate_from)
            self.setUpBeforeMigration()
            self._perform_actual_migration()
        except Exception:
            # Bring back the DB to the correct state if this setup part fails
            self._reset_database_and_schema()
            autogroup.CURRENT_AUTOGROUP = self.current_autogroup
            raise

    def _perform_actual_migration(self):
        """Perform the actual migration (upwards, to migrate_to).

        Must be called after we are properly set to be in migrate_from.
        """
        self.migrate_db_up(self.migrate_to)

    def migrate_db_up(self, destination):
        """
        Perform a migration upwards (upgrade) with alembic

        :param destination: the name of the destination migration
        """
        # Undo all previous real migration of the database
        with self.manager.alembic_config() as config:
            command.upgrade(config, destination)

    def migrate_db_down(self, destination):
        """
        Perform a migration downwards (downgrade) with alembic

        :param destination: the name of the destination migration
        """
        with self.manager.alembic_config() as config:
            command.downgrade(config, destination)

    def tearDown(self):
        """
        Resets both the database content and the schema to prepare for the
        next test
        """
        from aiida.orm import autogroup
        self._reset_database_and_schema()
        autogroup.CURRENT_AUTOGROUP = self.current_autogroup
        super().tearDown()

    def setUpBeforeMigration(self):  # pylint: disable=invalid-name
        """
        Anything to do before running the migrations.
        This is typically implemented in test subclasses.
        """

    def _reset_database_and_schema(self):
        """
        Bring back the DB to the correct state.

        It is important to also reset the database content to avoid hanging
        of tests.
        """
        self.clean_db()
        self.migrate_db_up('head')

    @property
    def current_rev(self):
        """
        Utility method to get the current revision string
        """
        from alembic.migration import MigrationContext  # pylint: disable=import-error
        with sa.ENGINE.begin() as connection:
            context = MigrationContext.configure(connection)
            current_rev = context.get_current_revision()
        return current_rev

    @staticmethod
    def get_auto_base():
        """
        Return the automap_base class that automatically inspects the current
        database and return SQLAlchemy Models.

        Note that these are NOT the ones in AiiDA SQLAlchemy models, so do not
        have the special methods that we define there (like .save()).
        """
        from alembic.migration import MigrationContext  # pylint: disable=import-error
        from sqlalchemy.ext.automap import automap_base  # pylint: disable=import-error,no-name-in-module

        with sa.ENGINE.begin() as connection:
            context = MigrationContext.configure(connection)
            bind = context.bind

            base = automap_base()
            # reflect the tables
            base.prepare(bind.engine, reflect=True)

            return base

    @staticmethod
    @contextmanager
    def get_session():
        """
        Return a session that is properly closed after use.
        """
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        with sa.ENGINE.begin() as connection:
            session = Session(connection.engine)
            yield session
            session.close()

    def get_current_table(self, table_name):
        """
        Return a Model instantiated at the correct migration.
        Note that this is obtained by inspecting the database and not
        by looking into the models file. So, special methods possibly defined
        in the models files/classes are not present.

        For instance, you can do::

          DbGroup = self.get_current_table('db_dbgroup')

        :param table_name: the name of the table.
        """
        base = self.get_auto_base()
        return getattr(base.classes, table_name)

    @staticmethod
    def get_node_array(node, name):
        return utils.load_numpy_array_from_repository(node.uuid, name)

    @staticmethod
    def set_node_array(node, name, array):
        """Store a new numpy array inside a node. Possibly overwrite the array if it already existed.

        Internally, it stores a name.npy file in numpy format.

        :param name: The name of the array.
        :param array: The numpy array to store.
        """
        utils.store_numpy_array_in_repository(node.uuid, name, array)
        attributes = node.attributes
        if attributes is None:
            attributes = {}
        attributes[f'array|{name}'] = list(array.shape)
        node.attributes = attributes
        flag_modified(node, 'attributes')


class TestBackwardMigrationsSQLA(TestMigrationsSQLA):
    """
    This is the equivalent of TestMigrationsSQLA for backward migrations.
    It assumes that the migrate_from revision is higher in the hierarchy
    than the migrate_to revision.
    """

    def _perform_actual_migration(self):
        """Perform the actual migration (downwards, to migrate_to).

        Must be called after we are properly set to be in migrate_from.
        """
        self.migrate_db_down(self.migrate_to)


class TestMigrationEngine(TestMigrationsSQLA):
    """
    Just a simple test to verify that the TestMigrationsSQLA class indeed
    works and moves between the expected migration revisions
    """
    migrate_from = 'b8b23ddefad4'  # b8b23ddefad4_dbgroup_name_to_label_type_to_type_string.py
    migrate_to = 'e72ad251bcdb'  # e72ad251bcdb_dbgroup_class_change_type_string_values.py

    def setUpBeforeMigration(self):
        """
        Cache the start revision
        """
        self.start_revision = self.current_rev

    def test_revision_numbers(self):
        """
        Check that we went to the correct version
        """
        self.assertEqual(self.start_revision, self.migrate_from)
        self.assertEqual(self.current_rev, self.migrate_to)


class TestMigrationSchemaVsModelsSchema(AiidaTestCase):
    """
    This class checks that the schema that results from a migration is the
    same generated by the models. This is important since migrations are
    frequently written by hand or extended manually and we have to ensure
    that the final result is what is conceived in the SQLA models.
    """
    # The path to the folder that contains the migration configuration (the
    # actual configuration - not the testing)
    migr_method_dir_path = None
    # The path of the migration configuration (the actual configuration - not
    # the testing)
    alembic_dpath = None
    # The alembic configuration needed for the migrations is stored here
    alembic_cfg_left = None

    # The URL of the databases
    db_url_left = None
    db_url_right = None

    def setUp(self):
        from sqlalchemydiff.util import get_temporary_uri
        from aiida.backends.sqlalchemy.migrations import versions

        self.migr_method_dir_path = os.path.dirname(os.path.realpath(manager.__file__))
        # Set the alembic script directory location
        self.alembic_dpath = os.path.join(self.migr_method_dir_path, manager.ALEMBIC_REL_PATH)  # pylint: disable=no-member

        # Constructing the versions directory
        versions_dpath = os.path.join(os.path.dirname(versions.__file__))

        # Setting dynamically the the path to the alembic configuration
        # (this is where the env.py file can be found)
        self.alembic_cfg_left = Config()
        self.alembic_cfg_left.set_main_option('script_location', self.alembic_dpath)
        # Setting dynamically the versions directory. These are the
        # migration scripts to pass from one version to the other. The
        # default ones are overridden with test-specific migrations.
        self.alembic_cfg_left.set_main_option('version_locations', versions_dpath)

        # The correction URL to the SQLA database of the current
        # AiiDA connection
        curr_db_url = sa.ENGINE.url

        # Create new urls for the two new databases
        self.db_url_left = get_temporary_uri(str(curr_db_url))
        self.db_url_right = get_temporary_uri(str(curr_db_url))

        # Put the correct database url to the database used by alembic
        self.alembic_cfg_left.set_main_option('sqlalchemy.url', self.db_url_left)

        # Database creation
        new_database(self.db_url_left)
        new_database(self.db_url_right)

    def tearDown(self):
        from sqlalchemydiff.util import destroy_database
        destroy_database(self.db_url_left)
        destroy_database(self.db_url_right)

    def test_model_and_migration_schemas_are_the_same(self):  # pylint: disable=invalid-name
        """Compare two databases.

        Compares the database obtained with all migrations against the
        one we get out of the models.  It produces a text file with the
        results to help debug differences.
        """
        from sqlalchemy.engine import create_engine  # pylint: disable=import-error,no-name-in-module
        from sqlalchemydiff import compare

        with create_engine(self.db_url_left).begin() as connection:
            self.alembic_cfg_left.attributes['connection'] = connection  # pylint: disable=unsupported-assignment-operation
            command.upgrade(self.alembic_cfg_left, 'head')

        engine_right = create_engine(self.db_url_right)
        Base.metadata.create_all(engine_right)
        engine_right.dispose()

        result = compare(self.db_url_left, self.db_url_right, set(['alembic_version']))

        self.assertTrue(
            result.is_match,
            "The migration database doesn't match to the one "
            'created by the models.\nDifferences: ' + result._dump_data(result.errors)  # pylint: disable=protected-access
        )


class TestProvenanceRedesignMigration(TestMigrationsSQLA):
    """Test the data migration part of the provenance redesign migration."""

    migrate_from = '140c971ae0a3'  # 140c971ae0a3_migrate_builtin_calculations
    migrate_to = '239cea6d2452'  # 239cea6d2452_provenance_redesign

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node_calc_job_known = DbNode(
                    type='calculation.job.arithmetic.add.ArithmeticAddCalculation.', user_id=user.id
                )
                node_calc_job_unknown = DbNode(type='calculation.job.unknown.PluginJobCalculation.', user_id=user.id)
                node_process = DbNode(type='calculation.process.ProcessCalculation.', user_id=user.id)
                node_work_chain = DbNode(type='calculation.work.WorkCalculation.', user_id=user.id)
                node_work_function = DbNode(
                    type='calculation.work.WorkCalculation.', attributes={'function_name': 'test'}, user_id=user.id
                )
                node_inline = DbNode(type='calculation.inline.InlineCalculation.', user_id=user.id)
                node_function = DbNode(type='calculation.function.FunctionCalculation.', user_id=user.id)

                session.add(node_calc_job_known)
                session.add(node_calc_job_unknown)
                session.add(node_process)
                session.add(node_work_chain)
                session.add(node_work_function)
                session.add(node_inline)
                session.add(node_function)
                session.commit()

                self.node_calc_job_known_id = node_calc_job_known.id
                self.node_calc_job_unknown_id = node_calc_job_unknown.id
                self.node_process_id = node_process.id
                self.node_work_chain_id = node_work_chain.id
                self.node_work_function_id = node_work_function.id
                self.node_inline_id = node_inline.id
                self.node_function_id = node_function.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_verify_migration(self):
        """Verify that type string of the Data node was successfully adapted."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                # Migration of calculation job with known plugin class
                node_calc_job_known = session.query(DbNode).filter(DbNode.id == self.node_calc_job_known_id).one()
                self.assertEqual(node_calc_job_known.type, 'node.process.calculation.calcjob.CalcJobNode.')
                self.assertEqual(node_calc_job_known.process_type, 'aiida.calculations:arithmetic.add')

                # Migration of calculation job with unknown plugin class
                node_calc_job_unknown = session.query(DbNode).filter(DbNode.id == self.node_calc_job_unknown_id).one()
                self.assertEqual(node_calc_job_unknown.type, 'node.process.calculation.calcjob.CalcJobNode.')
                self.assertEqual(node_calc_job_unknown.process_type, 'unknown.PluginJobCalculation')

                # Migration of very old `ProcessNode` class
                node_process = session.query(DbNode).filter(DbNode.id == self.node_process_id).one()
                self.assertEqual(node_process.type, 'node.process.workflow.workchain.WorkChainNode.')

                # Migration of old `WorkCalculation` class
                node_work_chain = session.query(DbNode).filter(DbNode.id == self.node_work_chain_id).one()
                self.assertEqual(node_work_chain.type, 'node.process.workflow.workchain.WorkChainNode.')

                # Migration of old `WorkCalculation` class used for work function
                node_work_function = session.query(DbNode).filter(DbNode.id == self.node_work_function_id).one()
                self.assertEqual(node_work_function.type, 'node.process.workflow.workfunction.WorkFunctionNode.')

                # Migration of old `InlineCalculation` class
                node_inline = session.query(DbNode).filter(DbNode.id == self.node_inline_id).one()
                self.assertEqual(node_inline.type, 'node.process.calculation.calcfunction.CalcFunctionNode.')

                # Migration of old `FunctionCalculation` class
                node_function = session.query(DbNode).filter(DbNode.id == self.node_function_id).one()
                self.assertEqual(node_function.type, 'node.process.workflow.workfunction.WorkFunctionNode.')

            finally:
                session.close()


class TestGroupRenamingMigration(TestMigrationsSQLA):
    """
    Test the migration that renames the DbGroup type strings
    """

    migrate_from = 'b8b23ddefad4'  # b8b23ddefad4_dbgroup_name_to_label_type_to_type_string.py
    migrate_to = 'e72ad251bcdb'  # e72ad251bcdb_dbgroup_class_change_type_string_values.py

    def setUpBeforeMigration(self):
        """
        Create the DbGroups with the old type strings
        """
        # Create group
        DbGroup = self.get_current_table('db_dbgroup')  # pylint: disable=invalid-name
        DbUser = self.get_current_table('db_dbuser')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                default_user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(default_user)
                session.commit()

                # test user group type_string: '' -> 'user'
                group_user = DbGroup(label='test_user_group', user_id=default_user.id, type_string='')
                session.add(group_user)
                # test data.upf group type_string: 'data.upf.family' -> 'data.upf'
                group_data_upf = DbGroup(
                    label='test_data_upf_group', user_id=default_user.id, type_string='data.upf.family'
                )
                session.add(group_data_upf)
                # test auto.import group type_string: 'aiida.import' -> 'auto.import'
                group_autoimport = DbGroup(
                    label='test_import_group', user_id=default_user.id, type_string='aiida.import'
                )
                session.add(group_autoimport)
                # test auto.run group type_string: 'autogroup.run' -> 'auto.run'
                group_autorun = DbGroup(
                    label='test_autorun_group', user_id=default_user.id, type_string='autogroup.run'
                )
                session.add(group_autorun)

                session.commit()

                # Store values for later tests
                self.group_user_pk = group_user.id
                self.group_data_upf_pk = group_data_upf.id
                self.group_autoimport_pk = group_autoimport.id
                self.group_autorun_pk = group_autorun.id

            finally:
                session.close()

    def test_group_string_update(self):
        """
        Test that the type strings are properly migrated
        """
        DbGroup = self.get_current_table('db_dbgroup')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                # test user group type_string: '' -> 'user'
                group_user = session.query(DbGroup).filter(DbGroup.id == self.group_user_pk).one()
                self.assertEqual(group_user.type_string, 'user')

                # test data.upf group type_string: 'data.upf.family' -> 'data.upf'
                group_data_upf = session.query(DbGroup).filter(DbGroup.id == self.group_data_upf_pk).one()
                self.assertEqual(group_data_upf.type_string, 'data.upf')

                # test auto.import group type_string: 'aiida.import' -> 'auto.import'
                group_autoimport = session.query(DbGroup).filter(DbGroup.id == self.group_autoimport_pk).one()
                self.assertEqual(group_autoimport.type_string, 'auto.import')

                # test auto.run group type_string: 'autogroup.run' -> 'auto.run'
                group_autorun = session.query(DbGroup).filter(DbGroup.id == self.group_autorun_pk).one()
                self.assertEqual(group_autorun.type_string, 'auto.run')
            finally:
                session.close()


class TestCalcAttributeKeysMigration(TestMigrationsSQLA):
    """Test the migration of the keys of certain attribute for ProcessNodes and CalcJobNodes."""

    migrate_from = 'e72ad251bcdb'  # e72ad251bcdb_dbgroup_class_change_type_string_values
    migrate_to = '7ca08c391c49'  # 7ca08c391c49_calc_job_option_attribute_keys

    KEY_RESOURCES_OLD = 'jobresource_params'
    KEY_RESOURCES_NEW = 'resources'
    KEY_PARSER_NAME_OLD = 'parser'
    KEY_PARSER_NAME_NEW = 'parser_name'
    KEY_PROCESS_LABEL_OLD = '_process_label'
    KEY_PROCESS_LABEL_NEW = 'process_label'
    KEY_ENVIRONMENT_VARIABLES_OLD = 'custom_environment_variables'
    KEY_ENVIRONMENT_VARIABLES_NEW = 'environment_variables'

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                self.resources = {'number_machines': 1}
                self.parser_name = 'aiida.parsers:parser'
                self.process_label = 'TestLabel'
                self.environment_variables = {}

                attributes = {
                    self.KEY_RESOURCES_OLD: self.resources,
                    self.KEY_PARSER_NAME_OLD: self.parser_name,
                    self.KEY_PROCESS_LABEL_OLD: self.process_label,
                    self.KEY_ENVIRONMENT_VARIABLES_OLD: self.environment_variables,
                }
                node_work = DbNode(type='node.process.workflow.WorkflowNode.', attributes=attributes, user_id=user.id)
                node_calc = DbNode(
                    type='node.process.calculation.calcjob.CalcJobNode.', attributes=attributes, user_id=user.id
                )
                # Create a node of a different type to ensure that its attributes are not updated
                node_other = DbNode(type='node.othernode.', attributes=attributes, user_id=user.id)

                session.add(node_work)
                session.add(node_calc)
                session.add(node_other)
                session.commit()

                self.node_work_id = node_work.id
                self.node_calc_id = node_calc.id
                self.node_other_id = node_other.id
            finally:
                session.close()

    def test_attribute_key_changes(self):
        """Verify that the keys are successfully changed of the affected attributes."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        not_found = tuple([0])

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                node_work = session.query(DbNode).filter(DbNode.id == self.node_work_id).one()
                self.assertEqual(node_work.attributes.get(self.KEY_PROCESS_LABEL_NEW), self.process_label)
                self.assertEqual(node_work.attributes.get(self.KEY_PROCESS_LABEL_OLD, not_found), not_found)

                node_calc = session.query(DbNode).filter(DbNode.id == self.node_calc_id).one()
                self.assertEqual(node_calc.attributes.get(self.KEY_PROCESS_LABEL_NEW), self.process_label)
                self.assertEqual(node_calc.attributes.get(self.KEY_PARSER_NAME_NEW), self.parser_name)
                self.assertEqual(node_calc.attributes.get(self.KEY_RESOURCES_NEW), self.resources)
                self.assertEqual(
                    node_calc.attributes.get(self.KEY_ENVIRONMENT_VARIABLES_NEW), self.environment_variables
                )
                self.assertEqual(node_calc.attributes.get(self.KEY_PROCESS_LABEL_OLD, not_found), not_found)
                self.assertEqual(node_calc.attributes.get(self.KEY_PARSER_NAME_OLD, not_found), not_found)
                self.assertEqual(node_calc.attributes.get(self.KEY_RESOURCES_OLD, not_found), not_found)
                self.assertEqual(node_calc.attributes.get(self.KEY_ENVIRONMENT_VARIABLES_OLD, not_found), not_found)

                # The following node should not be migrated even if its attributes have the matching keys because
                # the node is not a ProcessNode
                node_other = session.query(DbNode).filter(DbNode.id == self.node_other_id).one()
                self.assertEqual(node_other.attributes.get(self.KEY_PROCESS_LABEL_OLD), self.process_label)
                self.assertEqual(node_other.attributes.get(self.KEY_PARSER_NAME_OLD), self.parser_name)
                self.assertEqual(node_other.attributes.get(self.KEY_RESOURCES_OLD), self.resources)
                self.assertEqual(
                    node_other.attributes.get(self.KEY_ENVIRONMENT_VARIABLES_OLD), self.environment_variables
                )
                self.assertEqual(node_other.attributes.get(self.KEY_PROCESS_LABEL_NEW, not_found), not_found)
                self.assertEqual(node_other.attributes.get(self.KEY_PARSER_NAME_NEW, not_found), not_found)
                self.assertEqual(node_other.attributes.get(self.KEY_RESOURCES_NEW, not_found), not_found)
                self.assertEqual(node_other.attributes.get(self.KEY_ENVIRONMENT_VARIABLES_NEW, not_found), not_found)
            finally:
                session.close()


class TestDbLogMigrationRecordCleaning(TestMigrationsSQLA):
    """Test the migration of the keys of certain attribute for ProcessNodes and CalcJobNodes."""

    migrate_from = '7ca08c391c49'  # 7ca08c391c49_calc_job_option_attribute_keys
    migrate_to = '041a79fc615f'  # 041a79fc615f_dblog_cleaning

    def tearDown(self):
        """Need to manually delete all the workflows created for the test because the model does not exist any more.

        Because the model does not exist anymore, they are no longer being cleaned in the database reset of the test
        base class. To prevent foreign keys from other tables still referencing these tables, we have to make sure to
        clean them here manually, before we call the parent, which will call the standard reset database methods.
        """
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbWorkflow = self.get_auto_base().classes.db_dbworkflow  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                session.query(DbWorkflow).delete()
                session.commit()
            finally:
                session.close()

        super().tearDown()

    def setUpBeforeMigration(self):
        # pylint: disable=too-many-locals,too-many-statements
        import importlib
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module
        from aiida.backends.general.migrations.utils import dumps_json

        log_migration = importlib.import_module(
            'aiida.backends.sqlalchemy.migrations.versions.041a79fc615f_dblog_cleaning'
        )

        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name
        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbWorkflow = self.get_auto_base().classes.db_dbworkflow  # pylint: disable=invalid-name
        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                calc_1 = DbNode(type='node.process.calculation.CalculationNode.', user_id=user.id)
                param = DbNode(type='data.dict.Dict.', user_id=user.id)
                leg_workf = DbWorkflow(label='Legacy WorkflowNode', user_id=user.id)
                calc_2 = DbNode(type='node.process.calculation.CalculationNode.', user_id=user.id)

                session.add(calc_1)
                session.add(param)
                session.add(leg_workf)
                session.add(calc_2)
                session.commit()

                log_1 = DbLog(
                    loggername='CalculationNode logger',
                    objpk=calc_1.id,
                    objname='node.calculation.job.quantumespresso.pw.',
                    message='calculation node 1',
                    metadata={
                        'msecs': 719.0849781036377,
                        'objpk': calc_1.id,
                        'lineno': 350,
                        'thread': 140011612940032,
                        'asctime': '10/21/2018 12:39:51 PM',
                        'created': 1540118391.719085,
                        'levelno': 23,
                        'message': 'calculation node 1',
                        'objname': 'node.calculation.job.quantumespresso.pw.',
                    }
                )
                log_2 = DbLog(
                    loggername='something.else logger',
                    objpk=param.id,
                    objname='something.else.',
                    message='parameter data with log message'
                )
                log_3 = DbLog(
                    loggername='TopologicalWorkflow logger',
                    objpk=leg_workf.id,
                    objname='aiida.workflows.user.topologicalworkflows.topo.TopologicalWorkflow',
                    message='parameter data with log message'
                )
                log_4 = DbLog(
                    loggername='CalculationNode logger',
                    objpk=calc_2.id,
                    objname='node.calculation.job.quantumespresso.pw.',
                    message='calculation node 2',
                    metadata={
                        'msecs': 719.0849781036377,
                        'objpk': calc_2.id,
                        'lineno': 360,
                        'levelno': 23,
                        'message': 'calculation node 1',
                        'objname': 'node.calculation.job.quantumespresso.pw.',
                    }
                )
                # Creating two more log records that don't correspond to a node
                log_5 = DbLog(
                    loggername='CalculationNode logger',
                    objpk=(calc_2.id + 1000),
                    objname='node.calculation.job.quantumespresso.pw.',
                    message='calculation node 1000',
                    metadata={
                        'msecs': 718,
                        'objpk': (calc_2.id + 1000),
                        'lineno': 361,
                        'levelno': 25,
                        'message': 'calculation node 1000',
                        'objname': 'node.calculation.job.quantumespresso.pw.',
                    }
                )
                log_6 = DbLog(
                    loggername='CalculationNode logger',
                    objpk=(calc_2.id + 1001),
                    objname='node.calculation.job.quantumespresso.pw.',
                    message='calculation node 10001',
                    metadata={
                        'msecs': 722,
                        'objpk': (calc_2.id + 1001),
                        'lineno': 362,
                        'levelno': 24,
                        'message': 'calculation node 1001',
                        'objname': 'node.calculation.job.quantumespresso.pw.',
                    }
                )

                session.add(log_1)
                session.add(log_2)
                session.add(log_3)
                session.add(log_4)
                session.add(log_5)
                session.add(log_6)

                session.commit()

                # Storing temporarily information needed for the check at the test
                self.to_check = dict()

                # Keeping calculation & calculation log ids
                self.to_check['CalculationNode'] = (
                    calc_1.id,
                    log_1.id,
                    calc_2.id,
                    log_4.id,
                )

                # The columns to project
                cols_to_project = []
                for val in log_migration.values_to_export:
                    cols_to_project.append(getattr(DbLog, val))

                # Getting the serialized Dict logs
                param_data = session.query(DbLog).filter(DbLog.objpk == param.id
                                                         ).filter(DbLog.objname == 'something.else.'
                                                                  ).with_entities(*cols_to_project).one()
                serialized_param_data = dumps_json([(dict(list(zip(param_data.keys(), param_data))))])
                # Getting the serialized logs for the unknown entity logs (as the export migration fuction
                # provides them) - this should coincide to the above
                serialized_unknown_exp_logs = log_migration.get_serialized_unknown_entity_logs(connection)
                # Getting their number
                unknown_exp_logs_number = log_migration.get_unknown_entity_log_number(connection)
                self.to_check['Dict'] = (serialized_param_data, serialized_unknown_exp_logs, unknown_exp_logs_number)

                # Getting the serialized legacy workflow logs
                # yapf: disable
                leg_wf = session.query(DbLog).filter(DbLog.objpk == leg_workf.id).filter(
                    DbLog.objname == 'aiida.workflows.user.topologicalworkflows.topo.TopologicalWorkflow'
                ).with_entities(*cols_to_project).one()
                serialized_leg_wf_logs = dumps_json([(dict(list(zip(leg_wf.keys(), leg_wf))))])
                # Getting the serialized logs for the legacy workflow logs (as the export migration function
                # provides them) - this should coincide to the above
                serialized_leg_wf_exp_logs = log_migration.get_serialized_legacy_workflow_logs(connection)
                eg_wf_exp_logs_number = log_migration.get_legacy_workflow_log_number(connection)
                self.to_check['WorkflowNode'] = (serialized_leg_wf_logs, serialized_leg_wf_exp_logs,
                                                 eg_wf_exp_logs_number)

                # Getting the serialized logs that don't correspond to a DbNode record
                logs_no_node = session.query(DbLog).filter(
                    DbLog.id.in_([log_5.id, log_6.id])).with_entities(*cols_to_project)
                logs_no_node_list = list()
                for log_no_node in logs_no_node:
                    logs_no_node_list.append((dict(list(zip(log_no_node.keys(), log_no_node)))))
                serialized_logs_no_node = dumps_json(logs_no_node_list)

                # Getting the serialized logs that don't correspond to a node (as the export migration function
                # provides them) - this should coincide to the above
                serialized_logs_exp_no_node = log_migration.get_serialized_logs_with_no_nodes(connection)
                logs_no_node_number = log_migration.get_logs_with_no_nodes_number(connection)
                self.to_check['NoNode'] = (serialized_logs_no_node, serialized_logs_exp_no_node, logs_no_node_number)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_dblog_calculation_node(self):
        """
        Verify that after the migration there is only two log records left and verify that they corresponds to
        the CalculationNodes.
        """
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                # Check that only two log records exist
                self.assertEqual(session.query(DbLog).count(), 2, 'There should be two log records left')

                # Get the node id of the log record referencing the node and verify that it is the correct one
                dbnode_id_1 = session.query(DbLog).filter(
                    DbLog.id == self.to_check['CalculationNode'][1]).with_entities('dbnode_id').one()[0]
                self.assertEqual(dbnode_id_1, self.to_check['CalculationNode'][0], 'The the referenced node is not '
                                 'the expected one')
                dbnode_id_2 = session.query(DbLog).filter(
                    DbLog.id == self.to_check['CalculationNode'][3]).with_entities('dbnode_id').one()[0]
                self.assertEqual(dbnode_id_2, self.to_check['CalculationNode'][2], 'The the referenced node is not '
                                 'the expected one')
            finally:
                session.close()

    def test_dblog_correct_export_of_logs(self):
        """
        Verify that export log methods for legacy workflows, unknown entities and log records that
        don't correspond to nodes, work as expected
        """
        import json

        self.assertEqual(self.to_check['Dict'][0], self.to_check['Dict'][1])
        self.assertEqual(self.to_check['Dict'][2], 1)

        self.assertEqual(self.to_check['WorkflowNode'][0], self.to_check['WorkflowNode'][1])
        self.assertEqual(self.to_check['WorkflowNode'][2], 1)

        self.assertEqual(sorted(list(json.loads(self.to_check['NoNode'][0])), key=lambda k: k['id']),
                          sorted(list(json.loads(self.to_check['NoNode'][1])), key=lambda k: k['id']))
        self.assertEqual(self.to_check['NoNode'][2], 2)

    def test_metadata_correctness(self):
        """
        Verify that the metadata of the remaining records don't have an objpk and objmetadata values.
        """
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                metadata = list(session.query(DbLog).with_entities(getattr(DbLog, 'metadata')).all())
                # Verify that the objpk and objname are no longer part of the metadata
                for (m_res,) in metadata:
                    self.assertNotIn('objpk', m_res.keys(), 'objpk should not exist any more in metadata')
                    self.assertNotIn('objname', m_res.keys(), 'objname should not exist any more in metadata')

            finally:
                session.close()

class TestDbLogMigrationBackward(TestBackwardMigrationsSQLA):
    """
    Check that backward migrations work also for the DbLog migration(s).
    """
    migrate_from = '041a79fc615f'  # 041a79fc615f_dblog_cleaning
    migrate_to = '7ca08c391c49'  # e72ad251bcdb_dbgroup_class_change_type_string_values

    def setUpBeforeMigration(self):
        # pylint: disable=too-many-locals,too-many-statements
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name
        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                calc_1 = DbNode(type='node.process.calculation.CalculationNode.1', user_id=user.id)
                calc_2 = DbNode(type='node.process.calculation.CalculationNode.2', user_id=user.id)

                session.add(calc_1)
                session.add(calc_2)
                session.commit()

                log_1 = DbLog(
                    loggername='CalculationNode logger',
                    dbnode_id=calc_1.id,
                    message='calculation node 1',
                    metadata={
                        'msecs': 719.0849781036377,
                        'lineno': 350,
                        'thread': 140011612940032,
                        'asctime': '10/21/2018 12:39:51 PM',
                        'created': 1540118391.719085,
                        'levelno': 23,
                        'message': 'calculation node 1',
                    })
                log_2 = DbLog(
                    loggername='CalculationNode logger',
                    dbnode_id=calc_2.id,
                    message='calculation node 2',
                    metadata={
                        'msecs': 719.0849781036377,
                        'lineno': 360,
                        'levelno': 23,
                        'message': 'calculation node 1',
                    })

                session.add(log_1)
                session.add(log_2)

                session.commit()

                # Keeping what is needed to be verified at the test
                self.to_check = dict()
                self.to_check[log_1.id] = (log_1.dbnode_id, calc_1.type)
                self.to_check[log_2.id] = (log_2.dbnode_id, calc_2.type)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_objpk_objname(self):
        """
        This test verifies that the objpk and objname have the right values
        after a forward and a backward migration.
        """
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                for log_pk in self.to_check:
                    log_entry = session.query(DbLog).filter(DbLog.id == log_pk).one()
                    log_dbnode_id, node_type = self.to_check[log_pk]

                    self.assertEqual(
                        log_dbnode_id, log_entry.objpk,
                        'The dbnode_id ({}) of the 0024 schema version should be identical to the objpk ({}) of '
                        'the 0023 schema version.'.format(log_dbnode_id, log_entry.objpk))
                    self.assertEqual(
                        node_type, log_entry.objname,
                        'The type ({}) of the linked node of the 0024 schema version should be identical to the '
                        'objname ({}) of the 0023 schema version.'.format(node_type, log_entry.objname))
                    self.assertEqual(
                        log_dbnode_id, log_entry.metadata['objpk'],
                        'The dbnode_id ({}) of the 0024 schema version should be identical to the objpk ({}) of '
                        'the 0023 schema version stored in the metadata.'.format(log_dbnode_id,
                                                                                 log_entry.metadata['objpk']))
                    self.assertEqual(
                        node_type, log_entry.metadata['objname'],
                        'The type ({}) of the linked node of the 0024 schema version should be identical to the '
                        'objname ({}) of the 0023 schema version stored in the metadata.'.format(
                            node_type, log_entry.metadata['objname']))
            finally:
                session.close()

class TestDbLogUUIDAddition(TestMigrationsSQLA):
    """
    Test that the UUID column is correctly added to the DbLog table and that the uniqueness
    constraint is added without problems (if the migration arrives until 375c2db70663 then the
    constraint is added properly).
    """

    migrate_from = '041a79fc615f'  # 041a79fc615f_dblog_cleaning
    migrate_to = '375c2db70663'  # 375c2db70663_dblog_uuid_uniqueness_constraint

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name
        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                calc_1 = DbNode(type='node.process.calculation.CalculationNode.', user_id=user.id)
                calc_2 = DbNode(type='node.process.calculation.CalculationNode.', user_id=user.id)

                session.add(calc_1)
                session.add(calc_2)
                session.commit()

                log_1 = DbLog(loggername='CalculationNode logger', dbnode_id=calc_1.id, message='calculation node 1')
                log_2 = DbLog(loggername='CalculationNode logger', dbnode_id=calc_2.id, message='calculation node 2')

                session.add(log_1)
                session.add(log_2)

                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_dblog_unique_uuids(self):
        """
        Verify that the UUIDs of the log records are unique
        """
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbLog = self.get_auto_base().classes.db_dblog  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                l_uuids = list(session.query(DbLog).with_entities(getattr(DbLog, 'uuid')).all())
                s_uuids = set(l_uuids)
                self.assertEqual(len(l_uuids), len(s_uuids), 'The UUIDs are not all unique.')
            finally:
                session.close()

class TestDataMoveWithinNodeMigration(TestMigrationsSQLA):
    """Test the migration of Data nodes after the data module was moved within the node moduel."""

    migrate_from = '041a79fc615f'  # 041a79fc615f_dblog_update
    migrate_to = '6a5c2ea1439d'  # 6a5c2ea1439d_move_data_within_node_module

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node_calc = DbNode(type='node.process.calculation.calcjob.CalcJobNode.', user_id=user.id)
                node_data = DbNode(type='data.int.Int.', user_id=user.id)

                session.add(node_data)
                session.add(node_calc)
                session.commit()

                self.node_calc_id = node_calc.id
                self.node_data_id = node_data.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_node_type_string(self):
        """Verify that type string of the Data node was successfully adapted."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                # The data node should have been touched and migrated
                node_data = session.query(DbNode).filter(DbNode.id == self.node_data_id).one()
                self.assertEqual(node_data.type, 'node.data.int.Int.')

                # The calc node by contrast should not have been changed
                node_calc = session.query(DbNode).filter(DbNode.id == self.node_calc_id).one()
                self.assertEqual(node_calc.type, 'node.process.calculation.calcjob.CalcJobNode.')
            finally:
                session.close()


class TestTrajectoryDataMigration(TestMigrationsSQLA):
    """Test the migration of the symbols from numpy array to attribute for TrajectoryData nodes."""
    import numpy

    migrate_from = '37f3d4882837'  # 37f3d4882837_make_all_uuid_columns_unique
    migrate_to = 'ce56d84bcc35'  # ce56d84bcc35_delete_trajectory_symbols_array

    stepids = numpy.array([60, 70])
    times = stepids * 0.01
    positions = numpy.array(
        [[[0., 0., 0.], [0.5, 0.5, 0.5], [1.5, 1.5, 1.5]], [[0., 0., 0.], [0.5, 0.5, 0.5], [1.5, 1.5, 1.5]]])
    velocities = numpy.array(
        [[[0., 0., 0.], [0., 0., 0.], [0., 0., 0.]], [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5], [-0.5, -0.5, -0.5]]])
    cells = numpy.array([[[2., 0., 0.], [0., 2., 0.], [0., 0., 2.]], [[3., 0., 0.], [0., 3., 0.], [0., 0., 3.]]])

    def setUpBeforeMigration(self):
        import numpy
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node = DbNode(type='node.data.array.trajectory.TrajectoryData.', user_id=user.id)
                session.add(node)
                session.commit()

                symbols = numpy.array(['H', 'O', 'C'])

                self.set_node_array(node, 'steps', self.stepids)
                self.set_node_array(node, 'cells', self.cells)
                self.set_node_array(node, 'symbols', symbols)
                self.set_node_array(node, 'positions', self.positions)
                self.set_node_array(node, 'times', self.times)
                self.set_node_array(node, 'velocities', self.velocities)
                session.commit()

                self.node_uuid = node.uuid
            finally:
                session.close()

    def test_trajectory_symbols(self):
        """Verify that migration of symbols from repository array to attribute works properly."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                node = session.query(DbNode).filter(DbNode.uuid == self.node_uuid).one()

                self.assertSequenceEqual(node.attributes['symbols'], ['H', 'O', 'C'])
                self.assertSequenceEqual(self.get_node_array(node, 'velocities').tolist(), self.velocities.tolist())
                self.assertSequenceEqual(self.get_node_array(node, 'positions').tolist(), self.positions.tolist())
                with self.assertRaises(IOError):
                    self.get_node_array(node, 'symbols')

            finally:
                session.close()


class TestNodePrefixRemovalMigration(TestMigrationsSQLA):
    """Test the migration of Data nodes after the data module was moved within the node moduel."""

    migrate_from = 'ce56d84bcc35'  # ce56d84bcc35_delete_trajectory_symbols_array
    migrate_to = '61fc0913fae9'  # 61fc0913fae9_remove_node_prefix

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node_calc = DbNode(type='node.process.calculation.calcjob.CalcJobNode.', user_id=user.id)
                node_data = DbNode(type='node.data.int.Int.', user_id=user.id)

                session.add(node_data)
                session.add(node_calc)
                session.commit()

                self.node_calc_id = node_calc.id
                self.node_data_id = node_data.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_node_type_string(self):
        """Verify that type string of the Data node was successfully adapted."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                # Verify that the `node.` prefix has been dropped from both the data as well as the process node
                node_data = session.query(DbNode).filter(DbNode.id == self.node_data_id).one()
                self.assertEqual(node_data.type, 'data.int.Int.')

                node_calc = session.query(DbNode).filter(DbNode.id == self.node_calc_id).one()
                self.assertEqual(node_calc.type, 'process.calculation.calcjob.CalcJobNode.')
            finally:
                session.close()


class TestParameterDataToDictMigration(TestMigrationsSQLA):
    """Test the data migration after `ParameterData` was renamed to `Dict`."""

    migrate_from = '61fc0913fae9'  # 61fc0913fae9_remove_node_prefix
    migrate_to = 'd254fdfed416'  # d254fdfed416_rename_parameter_data_to_dict

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node = DbNode(type='data.parameter.ParameterData.', user_id=user.id)

                session.add(node)
                session.commit()

                self.node_id = node.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_type_string(self):
        """Verify that type string of the Data node was successfully adapted."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                node = session.query(DbNode).filter(DbNode.id == self.node_id).one()
                self.assertEqual(node.type, 'data.dict.Dict.')
            finally:
                session.close()


class TestLegacyJobCalcStateDataMigration(TestMigrationsSQLA):
    """Test the migration that performs a data migration of legacy `JobCalcState`."""

    migrate_from = '07fac78e6209'
    migrate_to = '26d561acd560'

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module
        from aiida.backends.general.migrations.calc_state import STATE_MAPPING

        self.state_mapping = STATE_MAPPING
        self.nodes = {}

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                for state in self.state_mapping:
                    node = DbNode(
                        node_type='process.calculation.calcjob.CalcJobNode.',
                        user_id=user.id,
                        attributes={'state': state}
                    )
                    session.add(node)
                    session.commit()

                    self.nodes[state] = node.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_migrated(self):
        """Verify that the `process_state`, `process_status` and `exit_status` are set correctly."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                for state, pk in self.nodes.items():
                    node = session.query(DbNode).filter(DbNode.id == pk).one()
                    attrs = node.attributes
                    self.assertEqual(attrs.get('process_state', None), self.state_mapping[state].process_state)
                    self.assertEqual(attrs.get('process_status', None), self.state_mapping[state].process_status)
                    self.assertEqual(attrs.get('exit_status', None), self.state_mapping[state].exit_status)
                    self.assertEqual(attrs.get('process_label'), 'Legacy JobCalculation')
                    self.assertIsNone(attrs.get('state', None))  # The old state should have been removed

                    exit_status = attrs.get('exit_status', None)
                    if exit_status is not None:
                        self.assertIsInstance(exit_status, int)
            finally:
                session.close()


class TestResetHash(TestMigrationsSQLA):
    """Test the migration that resets the node hash."""

    migrate_from = '26d561acd560'
    migrate_to = 'e797afa09270'

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module
        from aiida.backends.general.migrations.calc_state import STATE_MAPPING

        self.state_mapping = STATE_MAPPING
        self.nodes = {}

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node = DbNode(
                    node_type='process.calculation.calcjob.CalcJobNode.',
                    user_id=user.id,
                    extras={'something': 123, '_aiida_hash': 'abcd'}
                )
                session.add(node)
                session.commit()

                self.node_id = node.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_migrated(self):
        """Verify that only the _aiida_hash extra has been removed."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                node = session.query(DbNode).filter(DbNode.id == self.node_id).one()
                extras = node.extras
                self.assertEqual(extras.get('something'), 123)  # Other extras should be untouched
                self.assertNotIn('_aiida_hash', extras)  # The hash extra should have been removed
            finally:
                session.close()


class TestLegacyProcessAttributeMigration(TestMigrationsSQLA):
    """Test the migration that performs a data migration of legacy process attributes."""

    migrate_from = 'e797afa09270'
    migrate_to = 'e734dd5e50d7'

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node_process = DbNode(
                    node_type='process.calculation.calcjob.CalcJobNode.',
                    user_id=user.id,
                    attributes={
                        '_sealed': True,
                        '_finished': True,
                        '_failed': False,
                        '_aborted': False,
                        '_do_abort': False,
                    })

                # This is an "active" modern process, due to its `process_state` and should *not* receive the
                # `sealed` attribute
                node_process_active = DbNode(
                    node_type='process.calculation.calcjob.CalcJobNode.',
                    user_id=user.id,
                    attributes={
                        'process_state': 'created',
                        '_finished': True,
                        '_failed': False,
                        '_aborted': False,
                        '_do_abort': False,
                    })

                # Note that `Data` nodes should not have these attributes in real databases but the migration explicitly
                # excludes data nodes, which is what this test is verifying, by checking they are not deleted
                node_data = DbNode(
                    node_type='data.dict.Dict.',
                    user_id=user.id,
                    attributes={
                        '_sealed': True,
                        '_finished': True,
                        '_failed': False,
                        '_aborted': False,
                        '_do_abort': False,
                    })

                session.add(node_process)
                session.add(node_process_active)
                session.add(node_data)
                session.commit()

                self.node_process_id = node_process.id
                self.node_process_active_id = node_process_active.id
                self.node_data_id = node_data.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_migrated(self):
        """Verify that the attributes for process node have been deleted and `_sealed` has been changed to `sealed`."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                deleted_keys = ['_sealed', '_finished', '_failed', '_aborted', '_do_abort']

                node_process = session.query(DbNode).filter(DbNode.id == self.node_process_id).one()
                self.assertEqual(node_process.attributes['sealed'], True)
                for key in deleted_keys:
                    self.assertNotIn(key, node_process.attributes)

                node_process_active = session.query(DbNode).filter(DbNode.id == self.node_process_active_id).one()
                self.assertNotIn('sealed', node_process_active.attributes)
                for key in deleted_keys:
                    self.assertNotIn(key, node_process_active.attributes)

                node_data = session.query(DbNode).filter(DbNode.id == self.node_data_id).one()
                self.assertEqual(node_data.attributes.get('sealed', None), None)
                for key in deleted_keys:
                    self.assertIn(key, node_data.attributes)

            finally:
                session.close()


class TestSealUnsealedProcessesMigration(TestMigrationsSQLA):
    """Test the migration that performs a data migration of legacy process attributes."""

    migrate_from = 'e734dd5e50d7'
    migrate_to = '7b38a9e783e7'

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node_process = DbNode(
                    node_type='process.calculation.calcjob.CalcJobNode.',
                    user_id=user.id,
                    attributes={
                        'process_state': 'finished',
                        'sealed': True,
                    })

                # This is an "active" modern process, due to its `process_state` and should *not* receive the
                # `sealed` attribute
                node_process_active = DbNode(
                    node_type='process.calculation.calcjob.CalcJobNode.',
                    user_id=user.id,
                    attributes={
                        'process_state': 'created',
                    })

                # This is a legacy process that does not even have a `process_state`
                node_process_legacy = DbNode(
                    node_type='process.calculation.calcfunction.CalcFunctionNode.',
                    user_id=user.id,
                    attributes={}
                )

                # Note that `Data` nodes should not have these attributes in real databases but the migration explicitly
                # excludes data nodes, which is what this test is verifying, by checking they are not deleted
                node_data = DbNode(
                    node_type='data.dict.Dict.',
                    user_id=user.id,
                    attributes={}
                )

                session.add(node_process)
                session.add(node_process_active)
                session.add(node_process_legacy)
                session.add(node_data)
                session.commit()

                self.node_process_id = node_process.id
                self.node_process_active_id = node_process_active.id
                self.node_process_legacy_id = node_process_legacy.id
                self.node_data_id = node_data.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_migrated(self):
        """Verify that the attributes for process node have been deleted and `_sealed` has been changed to `sealed`."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                node_process = session.query(DbNode).filter(DbNode.id == self.node_process_id).one()
                self.assertEqual(node_process.attributes['sealed'], True)

                node_process_active = session.query(DbNode).filter(DbNode.id == self.node_process_active_id).one()
                self.assertNotIn('sealed', node_process_active.attributes)

                node_process_legacy = session.query(DbNode).filter(DbNode.id == self.node_process_legacy_id).one()
                self.assertEqual(node_process_legacy.attributes['sealed'], True)

                node_data = session.query(DbNode).filter(DbNode.id == self.node_data_id).one()
                self.assertNotIn('sealed', node_data.attributes)

            finally:
                session.close()


class TestDefaultLinkLabelMigration(TestMigrationsSQLA):
    """Test the migration that performs a data migration of legacy default link labels."""

    migrate_from = '91b573400be5'
    migrate_to = '118349c10896'

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbLink = self.get_auto_base().classes.db_dblink  # pylint: disable=invalid-name
        DbNode = self.get_auto_base().classes.db_dbnode  # pylint: disable=invalid-name
        DbUser = self.get_auto_base().classes.db_dbuser  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(user)
                session.commit()

                node_process = DbNode(node_type='process.calculation.calcjob.CalcJobNode.', user_id=user.id)
                node_data = DbNode(node_type='data.dict.Dict.', user_id=user.id)
                link = DbLink(input_id=node_data.id, output_id=node_process.id, type='input', label='_return')

                session.add(node_process)
                session.add(node_data)
                session.add(link)
                session.commit()

                self.node_process_id = node_process.id
                self.node_data_id = node_data.id
                self.link_id = link.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_data_migrated(self):
        """Verify that the attributes for process node have been deleted and `_sealed` has been changed to `sealed`."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbLink = self.get_auto_base().classes.db_dblink  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)
                link = session.query(DbLink).filter(DbLink.id == self.link_id).one()
                self.assertEqual(link.label, 'result')

            finally:
                session.close()


class TestGroupTypeStringMigration(TestMigrationsSQLA):
    """Test the migration that renames the DbGroup type strings."""

    migrate_from = '118349c10896'  # 118349c10896_default_link_label.py
    migrate_to = 'bf591f31dd12'  # bf591f31dd12_dbgroup_type_string.py

    def setUpBeforeMigration(self):
        """Create the DbGroups with the old type strings."""
        DbGroup = self.get_current_table('db_dbgroup')  # pylint: disable=invalid-name
        DbUser = self.get_current_table('db_dbuser')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                default_user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(default_user)
                session.commit()

                # test user group type_string: 'user' -> 'core'
                group_user = DbGroup(label='01', user_id=default_user.id, type_string='user')
                session.add(group_user)
                # test data.upf group type_string: 'data.upf' -> 'core.upf'
                group_data_upf = DbGroup(label='02', user_id=default_user.id, type_string='data.upf')
                session.add(group_data_upf)
                # test auto.import group type_string: 'auto.import' -> 'core.import'
                group_autoimport = DbGroup(label='03', user_id=default_user.id, type_string='auto.import')
                session.add(group_autoimport)
                # test auto.run group type_string: 'auto.run' -> 'core.auto'
                group_autorun = DbGroup(label='04', user_id=default_user.id, type_string='auto.run')
                session.add(group_autorun)

                session.commit()

                # Store values for later tests
                self.group_user_pk = group_user.id
                self.group_data_upf_pk = group_data_upf.id
                self.group_autoimport_pk = group_autoimport.id
                self.group_autorun_pk = group_autorun.id

            finally:
                session.close()

    def test_group_string_update(self):
        """Test that the type strings are properly migrated."""
        DbGroup = self.get_current_table('db_dbgroup')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                # test user group type_string: 'user' -> 'core'
                group_user = session.query(DbGroup).filter(DbGroup.id == self.group_user_pk).one()
                self.assertEqual(group_user.type_string, 'core')

                # test data.upf group type_string: 'data.upf' -> 'core.upf'
                group_data_upf = session.query(DbGroup).filter(DbGroup.id == self.group_data_upf_pk).one()
                self.assertEqual(group_data_upf.type_string, 'core.upf')

                # test auto.import group type_string: 'auto.import' -> 'core.import'
                group_autoimport = session.query(DbGroup).filter(DbGroup.id == self.group_autoimport_pk).one()
                self.assertEqual(group_autoimport.type_string, 'core.import')

                # test auto.run group type_string: 'auto.run' -> 'core.auto'
                group_autorun = session.query(DbGroup).filter(DbGroup.id == self.group_autorun_pk).one()
                self.assertEqual(group_autorun.type_string, 'core.auto')
            finally:
                session.close()


class TestGroupExtrasMigration(TestMigrationsSQLA):
    """Test migration to add the `extras` JSONB column to the `DbGroup` model."""

    migrate_from = 'bf591f31dd12'  # bf591f31dd12_dbgroup_type_string.py
    migrate_to = '0edcdd5a30f0'  # 0edcdd5a30f0_dbgroup_extras.py

    def setUpBeforeMigration(self):
        """Create a DbGroup."""
        DbGroup = self.get_current_table('db_dbgroup')  # pylint: disable=invalid-name
        DbUser = self.get_current_table('db_dbuser')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                default_user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(default_user)
                session.commit()

                group = DbGroup(label='01', user_id=default_user.id, type_string='user')
                session.add(group)
                session.commit()

                # Store values for later tests
                self.group_pk = group.id

            finally:
                session.close()

    def test_group_string_update(self):
        """Test that the model now has an extras column with empty dictionary as default."""
        DbGroup = self.get_current_table('db_dbgroup')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                group = session.query(DbGroup).filter(DbGroup.id == self.group_pk).one()
                self.assertEqual(group.extras, {})
            finally:
                session.close()


class TestNodeRepositoryMetadataMigration(TestMigrationsSQLA):
    """Test migration adding the `repository_metadata` column to the `Node` model."""

    migrate_from = '0edcdd5a30f0'  # 0edcdd5a30f0_dbgroup_extras.py
    migrate_to = '7536a82b2cc4'  # 7536a82b2cc4_add_node_repository_metadata.py

    def setUpBeforeMigration(self):
        """Create a single node before migration."""
        DbNode = self.get_current_table('db_dbnode')  # pylint: disable=invalid-name
        DbUser = self.get_current_table('db_dbuser')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                default_user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(default_user)
                session.commit()

                node = DbNode(user_id=default_user.id)
                session.add(node)
                session.commit()

                self.node_id = node.id

            finally:
                session.close()

    def test_add_node_repository_metadata(self):
        """Test that the column is added and null by default."""
        DbNode = self.get_current_table('db_dbnode')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                node = session.query(DbNode).filter(DbNode.id == self.node_id).one()
                assert hasattr(node, 'repository_metadata')
                assert node.repository_metadata == {}
            finally:
                session.close()


class TestRepositoryMigration(TestMigrationsSQLA):
    """Test migration of the old file repository to the disk object store."""

    migrate_from = '7536a82b2cc4'
    migrate_to = '1feaea71bd5a'

    def setUpBeforeMigration(self):
        from aiida.common.utils import get_new_uuid

        DbNode = self.get_current_table('db_dbnode')  # pylint: disable=invalid-name
        DbUser = self.get_current_table('db_dbuser')  # pylint: disable=invalid-name

        with self.get_session() as session:
            try:
                default_user = DbUser(email=f'{self.id()}@aiida.net')
                session.add(default_user)
                session.commit()

                # For some reasons, the UUIDs do not get created automatically through the column's default in the
                # migrations so we set it manually using the same method.
                node_01 = DbNode(user_id=default_user.id, uuid=get_new_uuid())
                node_02 = DbNode(user_id=default_user.id, uuid=get_new_uuid())
                node_03 = DbNode(user_id=default_user.id, uuid=get_new_uuid())
                node_04 = DbNode(user_id=default_user.id, uuid=get_new_uuid())
                node_05 = DbNode(user_id=default_user.id, uuid=get_new_uuid())

                session.add(node_01)
                session.add(node_02)
                session.add(node_03)  # Empty repository folder
                session.add(node_04)  # Both `path` and `raw_input` subfolder
                session.add(node_05)  # Both `path` and `raw_input` subfolder & `.gitignore` in `path`
                session.commit()

                assert node_01.uuid is not None
                assert node_02.uuid is not None
                assert node_03.uuid is not None
                assert node_04.uuid is not None
                assert node_05.uuid is not None

                self.node_01_pk = node_01.id
                self.node_02_pk = node_02.id
                self.node_03_pk = node_03.id
                self.node_04_pk = node_04.id
                self.node_05_pk = node_05.id

                utils.put_object_from_string(node_01.uuid, 'sub/path/file_b.txt', 'b')
                utils.put_object_from_string(node_01.uuid, 'sub/file_a.txt', 'a')
                utils.put_object_from_string(node_02.uuid, 'output.txt', 'output')

                os.makedirs(utils.get_node_repository_sub_folder(node_04.uuid, 'path'), exist_ok=True)
                os.makedirs(utils.get_node_repository_sub_folder(node_04.uuid, 'raw_input'), exist_ok=True)
                os.makedirs(utils.get_node_repository_sub_folder(node_05.uuid, 'path'), exist_ok=True)
                os.makedirs(utils.get_node_repository_sub_folder(node_05.uuid, 'raw_input'), exist_ok=True)

                utils.put_object_from_string(node_05.uuid, '.gitignore', 'test')
                with open(
                    os.path.join(
                        utils.get_node_repository_sub_folder(node_05.uuid, 'raw_input'), 'input.txt'),
                        'w',
                        encoding='utf-8',
                ) as handle:
                    handle.write('input')

                # Add a repository folder for a node that no longer exists - i.e. it may have been deleted.
                utils.put_object_from_string(get_new_uuid(), 'file_of_deleted_node', 'output')

            finally:
                session.close()

    def test_migration(self):
        """Test that the files are correctly migrated."""
        import hashlib
        DbNode = self.get_current_table('db_dbnode')  # pylint: disable=invalid-name
        DbSetting = self.get_current_table('db_dbsetting')  # pylint: disable=invalid-name

        repository_uuid_key = 'repository|uuid'

        with self.get_session() as session:
            try:
                node_01 = session.query(DbNode).filter(DbNode.id == self.node_01_pk).one()
                node_02 = session.query(DbNode).filter(DbNode.id == self.node_02_pk).one()
                node_03 = session.query(DbNode).filter(DbNode.id == self.node_03_pk).one()
                node_05 = session.query(DbNode).filter(DbNode.id == self.node_05_pk).one()

                assert node_01.repository_metadata == {
                    'o': {
                        'sub': {
                            'o': {
                                'path': {
                                    'o': {
                                        'file_b.txt': {
                                            'k': hashlib.sha256('b'.encode('utf-8')).hexdigest()
                                        }
                                    }
                                },
                                'file_a.txt': {
                                    'k': hashlib.sha256('a'.encode('utf-8')).hexdigest()
                                }
                            }
                        }
                    }
                }
                assert node_02.repository_metadata == {
                    'o': {
                        'output.txt': {
                            'k': hashlib.sha256('output'.encode('utf-8')).hexdigest()
                        }
                    }
                }
                assert node_03.repository_metadata == {}
                assert node_05.repository_metadata == {
                    'o': {
                        'input.txt': {
                            'k': hashlib.sha256('input'.encode('utf-8')).hexdigest()
                        }
                    }
                }

                for hashkey, content in (
                    (node_01.repository_metadata['o']['sub']['o']['path']['o']['file_b.txt']['k'], b'b'),
                    (node_01.repository_metadata['o']['sub']['o']['file_a.txt']['k'], b'a'),
                    (node_02.repository_metadata['o']['output.txt']['k'], b'output'),
                    (node_05.repository_metadata['o']['input.txt']['k'], b'input'),
                ):
                    assert utils.get_repository_object(hashkey) == content

                repository_uuid = session.query(DbSetting).filter(DbSetting.key == repository_uuid_key).one()
                assert repository_uuid is not None
                assert isinstance(repository_uuid.val, str)
            finally:
                session.close()


class TestComputerNameToLabelMigration(TestMigrationsSQLA):
    """Test the renaming of `name` to `label` for `DbComputer."""

    migrate_from = '1feaea71bd5a'  # 1feaea71bd5a_migrate_repository
    migrate_to = '535039300e4a'  # 5ddd24e52864_dbnode_type_to_dbnode_node_type

    def setUpBeforeMigration(self):
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbComputer = self.get_auto_base().classes.db_dbcomputer  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                computer = DbComputer(name='testing')

                session.add(computer)
                session.commit()

                self.computer_id = computer.id
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def test_migration(self):
        """Verify that the column was successfully renamed."""
        from sqlalchemy.orm import Session  # pylint: disable=import-error,no-name-in-module

        DbComputer = self.get_auto_base().classes.db_dbcomputer  # pylint: disable=invalid-name

        with sa.ENGINE.begin() as connection:
            try:
                session = Session(connection.engine)

                computer = session.query(DbComputer).filter(DbComputer.id == self.computer_id).one()
                self.assertEqual(computer.label, 'testing')
            finally:
                session.close()
