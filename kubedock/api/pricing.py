
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

from flask import Blueprint
from flask.views import MethodView
import json
import re

from ..core import db, ConnectionPool
from ..exceptions import APIError, PermissionDenied
from ..rbac import check_permission
from ..login import auth_required
from ..settings import KUBERDOCK_INTERNAL_USER
from ..utils import KubeUtils, register_api, atomic, all_request_params, NODE_STATUSES
from ..users import User
from ..validation import check_pricing_api, package_schema, kube_schema, \
    packagekube_schema
from ..billing.models import Package, Kube, PackageKube
from ..pods.models import Pod
from ..kapi import licensing
from ..kapi import collect


pricing = Blueprint('pricing', __name__, url_prefix='/pricing')


class PackageNotFound(APIError):
    message = 'Package not found'
    status_code = 404


class KubeNotFound(APIError):
    message = 'Kube not found'
    status_code = 404


class DuplicateName(APIError):
    pass


class DefaultPackageNotRemovable(APIError):
    pass


class DefaultKubeNotRemovable(APIError):
    pass


class OperationOnInternalKube(APIError):
    status_code = 403


class KubeInUse(APIError):
    pass


class PackageInUse(APIError):
    pass


# === PACKAGE ROUTINES ===


@pricing.route('/userpackage', methods=['GET'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('get_own', 'pricing')
def get_user_kube_types():
    user = KubeUtils.get_current_user()
    user = User.query.filter_by(username=user.username).first()
    if user is None:
        raise APIError('No such user', 404, 'UserNotFound')
    return {k.kube.name: k.kube.id for k in user.package.kubes}


class PackagesAPI(KubeUtils, MethodView):
    decorators = [KubeUtils.jsonwrap, auth_required]

    def get(self, package_id=None):
        with_kubes = all_request_params().get('with_kubes')
        with_internal = all_request_params().get('with_internal')
        if check_permission('get', 'pricing'):
            if package_id is None:
                return [p.to_dict(with_kubes=with_kubes,
                                  with_internal=with_internal)
                        for p in Package.query.all()]
            data = Package.query.get(package_id)
            if data is None:
                raise PackageNotFound()
            return data.to_dict(with_kubes=with_kubes,
                                with_internal=with_internal)
        elif check_permission('get_own', 'pricing'):
            user = KubeUtils.get_current_user()
            if with_internal:
                with_internal = user.username == KUBERDOCK_INTERNAL_USER
            if package_id is not None and package_id != user.package.id:
                raise PackageNotFound()  # user can get only own package
            package = user.package.to_dict(with_kubes=with_kubes,
                                           with_internal=with_internal)
            return [package] if package_id is None else package
        raise PermissionDenied()

    @atomic(APIError('Could not create package', 500), nested=False)
    @check_permission('create', 'pricing')
    def post(self):
        params = check_pricing_api(self._get_params(), package_schema)
        if Package.query.filter_by(name=params['name']).first() is not None:
            raise DuplicateName('Package with name \'{0}\' already exists'
                                .format(params['name']))
        package = Package(**params)
        if package.is_default:
            package.remove_default_flags()

        db.session.add(package)
        db.session.flush()
        return package.to_dict()

    @atomic(APIError('Could not update package', 500), nested=False)
    @check_permission('edit', 'pricing')
    def put(self, package_id):
        package = Package.query.get(package_id)
        if package is None:
            raise PackageNotFound()
        params = check_pricing_api(self._get_params(), package_schema,
                                   update=True)

        if 'name' in params:
            duplicate = Package.query.filter(Package.name == params['name'],
                                             Package.id != package_id).first()
            if duplicate is not None:
                raise DuplicateName('Package with name \'{0}\' already exists'
                                    .format(params['name']))

        is_default = params.get('is_default', None)
        if is_default:
            package.remove_default_flags()
        elif package.is_default and is_default is not None:
            raise DefaultPackageNotRemovable(
                'Setting "is_default" flag to false is forbidden. '
                'You can change default package by setting another package '
                'as default.')

        for key, value in params.iteritems():
            setattr(package, key, value)
        db.session.flush()
        return package.to_dict()

    @atomic(APIError('Could not delete package', 500), nested=False)
    @check_permission('delete', 'pricing')
    def delete(self, package_id):
        package = Package.query.get(package_id)
        if package is None:
            raise PackageNotFound()
        if package.users:
            raise PackageInUse('You have users with this package')
        if package.is_default:
            raise DefaultPackageNotRemovable(
                'Deleting of default package is forbidden. '
                'Set another package as default and try again')
        PackageKube.query.filter_by(package_id=package_id).delete()
        db.session.delete(package)

register_api(pricing, PackagesAPI, 'packages', '/packages/', 'package_id',
             'int')


@pricing.route('/packages/default', methods=['GET'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('get', 'pricing')
def get_default_package():
    package = Package.get_default()
    if package is None:
        raise PackageNotFound
    return package.to_dict()

# === KUBE ROUTINES ===


class KubesAPI(KubeUtils, MethodView):
    decorators = [KubeUtils.jsonwrap, auth_required]

    @check_permission('get', 'pricing')
    def get(self, kube_id=None):
        if kube_id is None:
            return [i.to_dict() for i in Kube.public_kubes()]
        item = Kube.query.get(kube_id)
        if item is None:
            raise KubeNotFound()
        return item.to_dict()

    @check_permission('create', 'pricing')
    def post(self):
        params = self._get_params()
        return add_kube(params)

    @atomic(APIError('Could not update kube', 500), nested=False)
    @check_permission('edit', 'pricing')
    def put(self, kube_id):
        kube = Kube.query.get(kube_id)
        if kube is None:
            raise KubeNotFound()
        if not Kube.is_kube_editable(kube.id):
            raise OperationOnInternalKube('Kube type is not editable')
        data = check_pricing_api(self._get_params(), kube_schema, update=True)
        if 'name' in data:
            duplicate = Kube.get_by_name(data['name'], Kube.id != kube_id)
            if duplicate is not None:
                raise DuplicateName(
                    'Kube with name \'{0}\' already exists. '
                    'Name should be unique'.format(data['name']))
        is_default = data.get('is_default', None)
        if is_default:
            _remove_is_default_kube_flags()
        elif kube.is_default and is_default is not None:
            raise DefaultKubeNotRemovable(
                'Setting "is_default" flag to false is forbidden. '
                'You can change default kube type by setting another kube '
                'type as default.')

        for key, value in data.items():
            setattr(kube, key, value)
        db.session.flush()
        kube.send_event('change')
        return kube.to_dict()

    @atomic(APIError('Could not delete kube', 500), nested=False)
    @check_permission('delete', 'pricing')
    def delete(self, kube_id):
        kube = Kube.query.get(kube_id)
        if kube is None:
            raise KubeNotFound()
        if kube.is_default:
            raise DefaultKubeNotRemovable(
                'Deleting of default kube type is forbidden. '
                'Set another kube type as default and try again')
        if not Kube.is_kube_editable(kube.id):
            raise OperationOnInternalKube('Kube type is not editable')
        if kube.pods:
            raise KubeInUse('Some pods use this kube type')
        if kube.nodes:
            raise KubeInUse('Some nodes use this kube type')
        PackageKube.query.filter_by(kube_id=kube_id).delete()
        db.session.delete(kube)

register_api(pricing, KubesAPI, 'kubes', '/kubes/', 'kube_id')


@pricing.route('/kubes/default', methods=['GET'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('get', 'pricing')
def get_default_kube():
    kube = Kube.get_default_kube()
    if kube is None:
        raise KubeNotFound()
    return kube.to_dict()


@atomic(APIError('Could not create kube', 500), nested=False)
def add_kube(data):
    data = check_pricing_api(data, kube_schema)
    if Kube.get_by_name(data['name']) is not None:
        raise DuplicateName('Kube type with name \'{0}\' already exists. '
                            'Name must be unique'.format(data['name']))

    kube = Kube(**data)
    if kube.is_default:
        # reset is_default flag for all kubes if new kube marked as default
        _remove_is_default_kube_flags()
    db.session.add(kube)
    db.session.flush()
    kube.send_event('add')
    return kube.to_dict()


# === PACKAGE KUBE ROUTINES ===


@pricing.route('/packages/<int:package_id>/kubes-by-id', methods=['GET'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('get', 'pricing')
def get_package_kube_ids(package_id):
    package = Package.query.get(package_id)
    if package is None:
        raise PackageNotFound()
    return [kube.kube_id for kube in package.kubes]


@pricing.route('/packages/<int:package_id>/kubes-by-name', methods=['GET'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('get', 'pricing')
def get_package_kube_names(package_id):
    package = Package.query.get(package_id)
    if package is None:
        raise PackageNotFound()
    return [package_kube.kube.name for package_kube in package.kubes]


class PackageKubesAPI(KubeUtils, MethodView):
    decorators = [KubeUtils.jsonwrap, auth_required]

    @check_permission('get', 'pricing')
    def get(self, package_id, kube_id=None):
        package = Package.query.get(package_id)
        if package is None:
            raise PackageNotFound()
        if kube_id is None:
            return [dict(pk.kube.to_dict(), kube_price=pk.kube_price)
                    for pk in package.kubes]
        try:
            return (dict(pk.kube.to_dict(), kube_price=pk.kube_price)
                    for pk in package.kubes
                    if pk.kube_id == int(kube_id)).next()
        except StopIteration:
            raise KubeNotFound()

    @atomic(APIError('Could not add kube type to package', 500), nested=False)
    @check_permission('create', 'pricing')
    def post(self, package_id):
        if Package.query.get(package_id) is None:
            raise PackageNotFound()
        params = self._get_params()
        if 'id' in params:
            params = check_pricing_api(params, packagekube_schema)
            kube_id = params['id']
            kube = Kube.query.get(kube_id)
            if kube is None:
                raise KubeNotFound()
            if not kube.is_public():
                raise OperationOnInternalKube(
                    'Kube type is not allowed to use it in packages')
        else:
            params = check_pricing_api(params,
                                       dict(kube_schema, **packagekube_schema))
            kube_id = add_kube({key: value for key, value in params.iteritems()
                                if key in kube_schema}, commit=False)['id']

        return _add_kube_type_to_package(package_id, kube_id,
                                         params['kube_price'])

    @atomic(APIError('Could not update kube type in package', 500),
            nested=False)
    @check_permission('edit', 'pricing')
    def put(self, package_id=None, kube_id=None):
        if Package.query.get(package_id) is None:
            raise PackageNotFound()
        kube = Kube.query.get(kube_id)
        if kube is None:
            raise KubeNotFound()
        if not kube.is_public():
            raise OperationOnInternalKube(
                'Kube type is not allowed to use it in packages')
        params = check_pricing_api(self._get_params(), packagekube_schema)

        return _add_kube_type_to_package(package_id, kube_id,
                                         params['kube_price'])

    @atomic(APIError('Could not remove kube type from package', 500),
            nested=False)
    @check_permission('delete', 'pricing')
    def delete(self, package_id, kube_id):
        package_kube = PackageKube.query.filter_by(package_id=package_id,
                                                   kube_id=kube_id).first()
        if package_kube is None:
            raise KubeNotFound('Kube type is not in the package')
        if User.query.filter(User.pods.any(Pod.kube == package_kube.kube),
                             User.package == package_kube.package).first():
            raise KubeInUse('Some users with this package have pods '
                            'with this kube type')
        db.session.delete(package_kube)

register_api(pricing, PackageKubesAPI, 'packagekubes',
             '/packages/<int:package_id>/kubes/', 'kube_id')


def _add_kube_type_to_package(package_id, kube_id, kube_price):
    package_kube = PackageKube.query.filter_by(package_id=package_id,
                                               kube_id=kube_id).first()
    if package_kube is None:
        package_kube = PackageKube(package_id=package_id, kube_id=kube_id)
        db.session.add(package_kube)
    package_kube.kube_price = kube_price
    db.session.flush()
    return package_kube.to_dict()


def _remove_is_default_kube_flags():
    Kube.query.update({Kube.is_default: None}, synchronize_session='fetch')


def _format_package_version(
        version,
        major_pattern=re.compile(r'^(\d+[\.\d\-]+\d+)(.*)'),
        minor_pattern=re.compile(r'(\.rc\.\d+)')):
    """We have to convert version strings like
    '1.0-0.el7.centos.rc.1.cloudlinux' to '1.0-0.rc.1' to show it for an end
    user.
    Also convert versions like '1.0.3-0.1.git61c6ac5.el7.centos.2' to
    '1.0.3-0.1'
    :param version: version string which, for example, can be get by executing
        rpm -q --qf "%{VERSION}-%{RELEASE}" packagename
    major_pattern - RE patter which extracts first version part (digits,
        dots, dashes until letters) and rest of the version string
    minor_pattern - RE pattern which tries to extract special release signs
        from rest of the version string returned by major pattern.
    """
    if not version:
        return version
    match = major_pattern.match(version)
    if not match:
        return version
    major_v, rest_part = match.groups()
    if rest_part:
        match = minor_pattern.search(rest_part)
        if not match:
            return major_v
    return major_v + ''.join(x for x in match.groups() if x is not None)


def get_collection(force=False, key='KDCOLLECTION'):
    """Get collected data from redis or directly (and then cache'em)"""
    redis = ConnectionPool.get_connection()
    data = redis.get(key)
    if not data or force:
        data = collect.collect()
        if data:
            redis.set(key, json.dumps(data))
        return data
    return json.loads(data)


def process_collection(data):
    result = {
        'status': 'unknown',
        'expiration': '',
        'type': '',
        'installationID': data.get('installation-id', '')
    }

    result.update({
        key: data.get(key) for key in ('platform', 'storage')
    })

    result['version'] = {
        dest_key or src_key: _format_package_version(data.get(src_key))
        for dest_key, src_key in
        (('KuberDock', 'kuberdock'), (None, 'kubernetes'), (None, 'docker'))
    }

    nodes = data.get('nodes', [])
    result['data'] = {
        'nodes': len(nodes),
        'cores': sum(n.get('cores', 0) for n in nodes),
        'memory': sum(int(n.get('memory', {}).get('total', 0)) for n in nodes),
        'containers': sum(
            n.get('user_containers', {}).get(NODE_STATUSES.running, 0) for n in nodes
        )
    }

    result['data'].update({
        'pods': data.get('pods', {}).get('total', 0),
        'apps': data.get('predefined-apps', {}).get('count', 0),
        'persistentVolume': data.get(
            'persistent-volumes', {}).get('total-size', 0)
    })

    result['data']['memory'] = "{0:.1f}".format(
        float(result['data']['memory']) / (1024 ** 3))  # Gb
    result['data'] = {k: [0, v] for k, v in result['data'].iteritems()}

    lic_info = licensing.get_license_info()
    if lic_info is None:
        return result

    lic = lic_info.get('data', {}).get('license', {})
    for key in 'status', 'expiration', 'type':
        result[key] = lic.get(key)

    default_value = 'unlimited'
    invalid = any(result.get(key) in ('none', 'expired')
                  for key in ('status', 'type'))

    for key, value in result['data'].iteritems():
        if invalid:
            value[0] = '-'
        else:
            value[0] = lic.get(key, default_value)
            if str(value[0]) == '0':
                value[0] = default_value
    return result


@pricing.route('/license', methods=['GET'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('read_private', 'system_settings')
def get_license():
    force = all_request_params().get('force', False)
    data = get_collection(force)
    return process_collection(data)


@pricing.route('/license/installation_id', methods=['POST'])
@auth_required
@KubeUtils.jsonwrap
@check_permission('write', 'system_settings')
def set_installation_id():
    params = all_request_params()
    installation_id = params.get('value')
    licensing.update_installation_id(installation_id)
    collected = collect.collect()
    res = collect.send(collected)
    if res['status'] != 'OK':
        raise APIError(res['data'])
    return process_collection(collected)
