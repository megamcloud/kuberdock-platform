
# KuberDock - is a platform that allows users to run applications using Docker
# container images and create SaaS / PaaS based on these applications.
# Copyright (C) 2017 Cloud Linux INC
#
# This file is part of KuberDock.
#
# KuberDock is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# KuberDock is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KuberDock; if not, see <http://www.gnu.org/licenses/>.

"""Tests for kapi.predefined_apps
"""
import unittest
from uuid import uuid4

import mock

from kubedock.kapi import apps
from kubedock.pods.models import Pod
from kubedock.testutils.fixtures import VALID_TEMPLATE1
from kubedock.testutils.testcases import DBTestCase
from kubedock.exceptions import PredefinedAppExc
from kubedock.utils import POD_STATUSES


VARIABLES = {
    'APP_NAME': 'WordPress',
    'MYSQL_PD_SIZE': 2,
    'VAR_IN_NAME': mock.ANY,
    'PD_RAND': mock.ANY,
    'WPENV1': 1,
    'TESTAUTO1': mock.ANY,
}


class FakeObj(object):
    id = 1
    name = 'test'
    template = VALID_TEMPLATE1

    def __init__(self, tpl=None):
        if tpl is not None:
            self.template = tpl

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'template': self.template}


class TestEntitiesFilled(unittest.TestCase):
    """Test that entities are filled with non-default values"""

    def setUp(self):
        patcher = mock.patch('kubedock.kapi.apps.PredefinedAppModel')
        self.addCleanup(patcher.stop)
        patcher.start()
        apps.PredefinedAppModel.query.filter_by().first = mock.Mock(
            return_value=FakeObj())

    def test_variable_is_substituted(self):
        """Variable values is replaced with one passed in values"""
        values = {'MYSQL_PD_SIZE': 200}
        app = apps.PredefinedApp.get(1)
        tpl = app.get_filled_template_for_plan(0, values)
        apps.PredefinedAppModel.query.filter_by()\
            .first.assert_called_once_with()
        vols = tpl['spec']['template']['spec']['volumes']
        value = [v['persistentDisk']['pdSize'] for v in vols
                 if v['name'].startswith('mysql-persistent-storage')][0]
        self.assertEqual(value, 200)

    def test_variable_is_by_default(self):
        """Variable values non-presented in values left by default"""
        app = apps.PredefinedApp.get(1)
        tpl = app.get_filled_template_for_plan(0, None)
        apps.PredefinedAppModel.query.filter_by()\
            .first.assert_called_once_with()
        vols = tpl['spec']['template']['spec']['volumes']
        value = [v['persistentDisk']['pdSize'] for v in vols
                 if v['name'].startswith('mysql-persistent-storage')][0]
        self.assertEqual(value, 2)


class TestFillingWorkflow(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch('kubedock.kapi.apps.PredefinedAppModel')
        self.addCleanup(patcher.stop)
        patcher.start()
        apps.PredefinedAppModel.query.filter_by().first = mock.Mock(
            return_value=FakeObj())

    @mock.patch('kubedock.kapi.apps.PredefinedApp'
                '._get_filled_template_for_plan')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._expand_plans')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._apply_package')
    def test_when_filling_template_flow_fulfilled(self, ap, ep, ft):
        ft.return_value = {'kuberdock': {'appPackages': ['a_plan']}}
        pa = apps.PredefinedApp.get(1)
        pa.get_filled_template_for_plan(0, {})
        ft.assert_called_once_with(0, {})
        ep.assert_called_once_with({'kuberdock': {'kuberdock_template_id': 1}},
                                   with_info=False)
        ap.assert_called_once_with({'kuberdock': {'kuberdock_template_id': 1}},
                                   'a_plan')

    @mock.patch('kubedock.kapi.apps.PredefinedApp._expand_plans')
    def test_exception_raised_if_no_such_plan(self, ep):
        pa = apps.PredefinedApp.get(1)
        with self.assertRaises(PredefinedAppExc.NoSuchAppPackage):
            pa.get_filled_template_for_plan(8, {})
        ep.assert_not_called()

    @mock.patch('kubedock.kapi.apps.PredefinedApp._fill_template')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._squeeze_plans')
    def test_filling_with_values_flow(self, sp, ft):
        sp.return_value = mock.sentinel.SQUEEZED
        pa = apps.PredefinedApp.get(1)
        pa._get_filled_template_for_plan(8, 'test')
        sp.assert_called_once_with(8)
        ft.assert_called_once_with(
            loaded=mock.sentinel.SQUEEZED, values='test')

    @mock.patch('kubedock.kapi.apps.PredefinedApp._fill_template')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._squeeze_plans')
    def test_filling_defaults_flow(self, sp, ft):
        sp.return_value = mock.sentinel.SQUEEZED
        ft.return_value = mock.sentinel.FILLED
        pa = apps.PredefinedApp.get(1)
        pa._used_entities_by_plans[8] = mock.sentinel.USED_ENTITIES
        rv = pa._get_filled_template_for_plan(8)
        sp.assert_called_once_with(8)
        ft.assert_called_once_with(loaded=mock.sentinel.SQUEEZED,
                                   used_entities=mock.sentinel.USED_ENTITIES)
        self.assertTrue(rv is mock.sentinel.FILLED)

    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_loaded_template')
    def test_filling_flow(self, lt):
        pa = apps.PredefinedApp.get(1)
        lt.return_value = {'foo': 'bar'}
        rv = pa._fill_template()
        self.assertDictEqual(rv, dict(lt.return_value, appVariables={}))
        lt.assert_called_once_with()

    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_loaded_template')
    def test_filling_flow_with_provided_loaded_template(self, lt):
        pa = apps.PredefinedApp.get(1)
        loaded = {'foo': 'bar'}
        rv = pa._fill_template(loaded=loaded)
        self.assertDictEqual(rv, dict(loaded, appVariables={}))
        lt.assert_not_called()


class TestPodIsBaseOfTemplate(unittest.TestCase):
    """
    PredefinedApp.is_template_of is expected to return False if yaml could not
    be created using specified template.
    """
    def setUp(self):
        patcher = mock.patch('kubedock.kapi.apps.PredefinedAppModel')
        self.addCleanup(patcher.stop)
        patcher.start()
        apps.PredefinedAppModel.query.filter_by().first = mock.Mock(
            return_value=FakeObj())

    def test_template_is_base_of_data(self):
        values = {'MYSQL_PD_SIZE': 32}
        tpl = apps.PredefinedApp.get(1).get_filled_template_for_plan(0, values)
        self.assertTrue(apps.PredefinedApp.get(1).is_template_for(tpl))


@mock.patch('kubedock.kapi.apps.PredefinedAppModel')
class TestHowTemplateIsPreprocessed(unittest.TestCase):

    def test_escaping(self, dbo):
        tpl = """
        $VAR|default:0|yea$
        $$NOT_VAR|default:0|nope$$
        $$$VAR_IN_DOLLARS|default:0|wow$$$"""
        obj = FakeObj(tpl)
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pa = apps.PredefinedApp.get(1)
        rv = pa._get_preprocessed_template()
        self.assertEqual(set(pa._entities.keys()), set(
            ['VAR', 'VAR_IN_DOLLARS']))
        self.assertEqual(rv, """
        {0}
        $NOT_VAR|default:0|nope$
        ${1}$""".format(
            pa._entities['VAR'].uid, pa._entities['VAR_IN_DOLLARS'].uid))

    def test_second_definition(self, dbo):
        tpl = """
        $VAR|default:0|yea$
        $VAR|default:1|nope$"""
        obj = FakeObj(tpl)
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pa = apps.PredefinedApp.get(1)
        pa._get_preprocessed_template()
        self.assertEqual(pa._entities['VAR'].label, 'yea')

    def test_undefinined_field_raises_exception(self, dbo):
        tpl = """
        $VAR$"""
        obj = obj = FakeObj(tpl)
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pa = apps.PredefinedApp.get(1)
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._get_preprocessed_template()


@mock.patch('kubedock.kapi.apps.PredefinedAppModel')
class TestHowTemplateIsLoaded(unittest.TestCase):

    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_preprocessed_template')
    def test_load(self, pt, dbo):
        obj = FakeObj('template')
        fields = {
            'VAR_1': apps.PredefinedApp.TemplateField(
                'VAR_1', '0', 'first var label'),
            'VAR_2': apps.PredefinedApp.TemplateField(
                'VAR_2', '0', 'second var label')}
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pt.return_value = """
        a: {0}
        b: just some string
        c:
          - d: |
              string with {1}
            e: {1}
        """.format(fields['VAR_1'].uid, fields['VAR_2'].uid)
        pa = apps.PredefinedApp.get(1)
        pa._entities = fields
        pa._entities_by_uid = {fields['VAR_1'].uid: fields['VAR_1'],
                               fields['VAR_2'].uid: fields['VAR_2']}
        rv = pa._get_loaded_template()

        # All UID that are plain YAML-scalars must be replaced with appropriate
        # TemplateField instace. All UID's inside strings must be left as-is.
        self.assertEqual(rv, {
            'a': fields['VAR_1'],
            'b': 'just some string',
            'c': [{
                'd': 'string with {0}\n'.format(fields['VAR_2'].uid),
                'e': fields['VAR_2'],
            }]})
        pt.assert_called_once_with()


@mock.patch('kubedock.kapi.apps.PredefinedAppModel')
class TestHowTemplateIsFilled(unittest.TestCase):

    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_loaded_template')
    def test_fill_defaults(self, lt, dbo):
        obj = FakeObj('template')
        fields = {
            'VAR_1': apps.PredefinedApp.TemplateField(
                'VAR_1', 'default-1', 'first var label'),
            'VAR_2': apps.PredefinedApp.TemplateField(
                'VAR_2', 'default-2', 'second var label'),
            'VAR_3': apps.PredefinedApp.TemplateField(
                'VAR_3', 'default-2', 'third var label')}
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        lt.return_value = {
            'a': fields['VAR_1'],
            'b': 'just some string',
            'c': [{
                'd': 'string with {0}\n'.format(fields['VAR_2'].uid),
                'e': fields['VAR_2']}]}
        pa = apps.PredefinedApp.get(1)
        pa._entities = fields
        pa._entities_by_uid = {fields['VAR_1'].uid: fields['VAR_1'],
                               fields['VAR_2'].uid: fields['VAR_2'],
                               fields['VAR_3'].uid: fields['VAR_3']}
        rv = pa._get_filled_template()
        self.assertEqual(rv, {
            'a': 'default-1',
            'b': 'just some string',
            'c': [{
                'd': 'string with default-2\n',
                'e': 'default-2',
            }],
            'appVariables': {name: field.default
                             for name, field in pa._used_entities.items()}
        })
        lt.assert_called_once_with()
        self.assertEqual(
            pa._used_entities,
            {f.name: f for f in fields.values() if f.name != 'VAR_3'})


@mock.patch('kubedock.kapi.apps.PredefinedAppModel')
class TestCommonTemplateRoutines(unittest.TestCase):

    def test_get_plans_without_params(self, dbo):
        tpl = """
        kuberdock:
          appPackages: PACKAGES"""
        obj = FakeObj(tpl)
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pa = apps.PredefinedApp.get(1)
        plans = pa._get_plans()
        self.assertEqual(plans, 'PACKAGES')

    def test_exception_raised_when_no_plans(self, dbo):
        tpl = """
        kuberdock:
          missingAppPackages: NOPE"""
        obj = FakeObj(tpl)
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pa = apps.PredefinedApp.get(1)
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._get_plans()

    @mock.patch('kubedock.kapi.apps.yaml.safe_load')
    def test_when_template_is_passed_yaml_not_processed(self, ysl, dbo):
        obj = FakeObj()
        dbo.query.filter_by().first = mock.Mock(return_value=obj)
        pa = apps.PredefinedApp.get(1)
        pa._get_plans({'kuberdock': {'appPackages': [{'one': 'two'}]}})
        ysl.assert_not_called()


class TestLoadedPlans(unittest.TestCase):
    """
    PredefinedApp.get_loaded_plans method is expeced to return plans prepared
    for web-page displaying.
    """
    def setUp(self):
        for mod in 'PredefinedAppModel', 'Kube':
            patcher = mock.patch.object(apps, mod)
            self.addCleanup(patcher.stop)
            patcher.start()
        apps.PredefinedAppModel.query.filter_by().first = mock.Mock(
            return_value=FakeObj())
        apps.Kube.get_default_kube_type = mock.Mock(
            return_value=2048)

    @mock.patch('kubedock.kapi.apps.deepcopy')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._fill_template')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._expand_plans')
    def test_get_plans_flow(self, ep, ft, dc):
        ft.return_value = mock.sentinel.FILLED
        dc.return_value = mock.sentinel.DEEPCOPY
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        dc.assert_called_once_with(mock.sentinel.FILLED)
        ep.assert_called_once_with(mock.sentinel.DEEPCOPY)
        self.assertEqual(ft.call_count, 1)

    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_plans')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_template_spec')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._equalize_containers')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._fill_persistent_disks')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._calculate_info')
    def test_plans_expansion(self, ci, fpd, eq, gts, gp):
        pod_entity = {'containers': [], 'persistentDisks': [],
                      'name': None, 'kubeType': 2048}
        gp.return_value = [{}]
        gts.return_value = mock.sentinel.SPEC
        pa = apps.PredefinedApp.get(1)
        expanded = pa._expand_plans(mock.sentinel.FILLED, with_info=False)
        gp.assert_called_once_with(mock.sentinel.FILLED)
        gts.assert_called_once_with(mock.sentinel.FILLED)
        eq.assert_called_once_with(mock.sentinel.SPEC, pod_entity)
        fpd.assert_called_once_with(mock.sentinel.SPEC, pod_entity)
        ci.assert_not_called()
        self.assertEqual(apps.Kube.get_default_kube_type.call_count, 1)
        self.assertTrue('goodFor' in expanded[0] and
                        expanded[0]['goodFor'] == '')
        self.assertTrue('publicIP' in expanded[0] and
                        expanded[0]['publicIP'] is True)

    @mock.patch('kubedock.kapi.apps.PredefinedApp._has_public_ports')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_package')
    @mock.patch('kubedock.kapi.apps.PredefinedApp._get_kube_by_id')
    def test_get_plans_calculation(self, gkbi, gp, hpp):
        gkbi.return_value = {
            'cpu': 2.0, 'memory': 100, 'disk_space': 4, 'price': 2.0}
        gp.return_value = type('Package', (object,), {
            'price_pstorage': 10, 'price_ip': 10, 'period': 'M',
            'prefix': 'P', 'suffix': 'S'})
        hpp.return_value = True
        pa = apps.PredefinedApp.get(1)
        plan = {'pods': [{'containers': [{'kubes': 2}, {'kubes': 5}],
                'persistentDisks': [{'pdSize': 4}, {'pdSize': 3}]}]}
        pa._calculate_info(plan)
        gkbi.assert_called_once_with(2048)
        self.assertTrue(hpp.called)
        self.assertTrue(gp.called)
        self.assertEqual(gkbi.call_count, 1)
        self.assertTrue('info' in plan)
        self.assertEqual(plan['info']['cpu'], 14.0)
        self.assertEqual(plan['info']['memory'], 700)
        self.assertEqual(plan['info']['diskSpace'], 28)
        self.assertEqual(plan['info']['price'], 94.0)


class TestCheckPlans(DBTestCase):

    def setUp(self):
        patcher = mock.patch('kubedock.kapi.apps.PredefinedAppModel')
        self.addCleanup(patcher.stop)
        patcher.start()
        apps.PredefinedAppModel.query.filter_by().first = mock.Mock(
            return_value=FakeObj())

    def test_check_kuberdock_section_valid(self):
        pa = apps.PredefinedApp.get(1)
        pa._validate_template()

    def test_check_plans_2_recommended(self):
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        app_packages = pa._filled_template['kuberdock']['appPackages']
        app_packages[1]['recommended'] = True
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._validate_template()

    def test_check_plans_unknown_pod(self):
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        pod = pa._filled_template['kuberdock']['appPackages'][0]['pods'][0]
        pod['name'] = 'invalid'
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._validate_template()

    def test_check_plans_unknown_container(self):
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        pod = pa._filled_template['kuberdock']['appPackages'][0]['pods'][0]
        pod['containers'][0]['name'] = 'invalid'
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._validate_template()

    def test_check_plans_unknown_pd(self):
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        pod = pa._filled_template['kuberdock']['appPackages'][0]['pods'][0]
        pod['persistentDisks'][0]['name'] = 'invalid'
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._validate_template()

    def test_check_plans_invalid_kube_type(self):
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        pod = pa._filled_template['kuberdock']['appPackages'][0]['pods'][0]
        pod['kubeType'] = -8
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._validate_template()

    def test_check_kuberdock_section_invalid_package_id(self):
        other_kube = self.fixtures.kube_type()
        pa = apps.PredefinedApp.get(1)
        pa.get_plans()
        pod = pa._filled_template['kuberdock']['appPackages'][0]['pods'][0]
        pod['kubeType'] = other_kube.id
        with self.assertRaises(PredefinedAppExc.InvalidTemplate):
            pa._validate_template()


@mock.patch('kubedock.kapi.apps.PodCollection')
@mock.patch('kubedock.kapi.apps.check_new_pod_data')
@mock.patch('kubedock.kapi.apps.dispatch_kind')
class TestStartPodFromYAML(DBTestCase):
    def setUp(self):
        self.user, _ = self.fixtures.user_fixtures()

    def test_dry_run(self, dispatch_kind, check_new_pod_data, PodCollection):
        self.assertEqual(
            apps.start_pod_from_yaml({}, self.user, dry_run=True),
            PodCollection.return_value.add.return_value)

        check_new_pod_data.assert_called_once_with(
            dispatch_kind.return_value, self.user)
        PodCollection.assert_called_once_with(self.user)
        PodCollection.return_value.add.assert_called_once_with(
            check_new_pod_data.return_value, dry_run=True)


def fake_pod(**kwargs):
    parents = kwargs.pop('use_parents', ())
    return type('Pod', parents,
                dict({
                    'namespace': 'n',
                    'owner': 'u',
                    'id': 'u',
                    'status': POD_STATUSES.running,
                }, **kwargs))()


class TestPodConfig(DBTestCase):

    @mock.patch('kubedock.kapi.apps.PredefinedApp._update_IPs', mock.Mock())
    @mock.patch('kubedock.kapi.apps.update_plan_async', mock.Mock())
    @mock.patch('kubedock.kapi.apps.PodCollection.update', mock.Mock())
    @mock.patch('kubedock.kapi.apps.PodCollection._get_namespaces',
                mock.MagicMock())
    @mock.patch.object(apps.STORAGE_CLASS, 'is_pv_resizable',
                       mock.Mock(return_value=True))
    @mock.patch('kubedock.kapi.apps.change_pv_size')
    def test_new_sizes(self, change_pv_size):
        pod_id = str(uuid4())
        db_app = self.fixtures.predefined_app(template=VALID_TEMPLATE1)
        self.fixtures.pod(
            id=pod_id,
            owner_id=1,
            name='pod1',
            kube_id=0,
            template_id=db_app.id,
            config={
                'volumes_public': [
                    {
                        'persistentDisk': {
                            'pdName': 'mysql-persistent-storage',
                            'pdSize': 2
                        },
                        'name': 'mysql-persistent-storage'
                    },
                    {
                        'persistentDisk': {
                            'pdName': 'wordpress-persistent-storage',
                            'pdSize': 1
                        },
                        'name': 'wordpress-persistent-storage'
                    }
                ],
                "volumes": [
                    {
                        "name": "pd1",
                        "annotation": {
                            "localStorage": {
                                "path": "/var/lib/kuberdock/storage/3/pd1",
                                "name": "mysql-persistent-storage",
                                "size": 2
                            }
                        }
                    }, {
                        "name": "pd2",
                        "annotation": {
                            "localStorage": {
                                "path": "/var/lib/kuberdock/storage/3/pd2",
                                "name": "wordpress-persistent-storage",
                                "size": 1
                            }
                        }
                    }
                ],
                'containers': []
            }
        )
        self.fixtures.persistent_disk(
            name='mysql-persistent-storage',
            drive_name='3/mysql-persistent-storage',
            owner_id=1,
            size=2
        )

        pd2 = self.fixtures.persistent_disk(
            name='wordpress-persistent-storage',
            drive_name='3/wordpress-persistent-storage',
            owner_id=1,
            size=1
        )

        apps.AppInstance(pod_id).update_plan(plan_id=1, async=False)
        change_pv_size.assert_called_once_with(pd2.id, 2, dry_run=False)

    @mock.patch('kubedock.kapi.apps.PredefinedApp._update_IPs', mock.Mock())
    @mock.patch('kubedock.kapi.apps.update_plan_async', mock.Mock())
    @mock.patch('kubedock.kapi.apps.PodCollection.update', mock.Mock())
    @mock.patch('kubedock.kapi.apps.PodCollection._get_namespaces',
                mock.MagicMock())
    def test_set_public_ip(self):
        pod_id = str(uuid4())
        db_app = self.fixtures.predefined_app(template=VALID_TEMPLATE1)
        self.fixtures.pod(
            id=pod_id,
            owner_id=1,
            name='pod1',
            kube_id=0,
            template_id=db_app.id,
            config={
                'volumes_public': [],
                "volumes": [],
                'containers': [
                    {"sourceUrl": "hub.docker.com/_/mysql",
                     "name": "mysql",
                     "image": "mysql:5",
                     "kubes": 2,
                     "workingDir": "/data",
                     "readinessProbe": {
                         "initialDelaySeconds": 1,
                         "tcpSocket": {"port": 3306}
                     },
                     "ports": [{"podPort": 3306, "containerPort": 3306,
                                "isPublic": False}]
                     },
                    {"sourceUrl": "hub.docker.com/_/wordpress",
                     "name": "wordpress",
                     "image": "wordpress:5",
                     "kubes": 2,
                     "workingDir": "/data",
                     "readinessProbe": {
                         "initialDelaySeconds": 1,
                         "tcpSocket": {"port": 80}
                     },
                     "ports": [{"podPort": 80, "containerPort": 80,
                                "isPublic": False}]
                     }
                ]
            }
        )

        pod_db = Pod.query.get(pod_id)
        config = pod_db.get_dbconfig()
        wordpress_container = (c for c in config['containers']
                               if c['name'] == 'wordpress').next()
        self.assertFalse(wordpress_container['ports'][0].get('isPublic',
                                                             False))

        apps.AppInstance(pod_id).update_plan(plan_id=1, async=False)

        pod_db = Pod.query.get(pod_id)
        config = pod_db.get_dbconfig()
        wordpress_container = (c for c in config['containers']
                               if c['name'] == 'wordpress').next()
        mysql_container = (c for c in config['containers']
                           if c['name'] == 'mysql').next()

        self.assertTrue(wordpress_container['ports'][0].get('isPublic'))
        self.assertFalse(mysql_container['ports'][0].get('isPublic', False))

if __name__ == '__main__':
    unittest.main()
