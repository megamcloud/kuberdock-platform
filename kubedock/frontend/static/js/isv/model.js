/*
 * KuberDock - is a platform that allows users to run applications using Docker
 * container images and create SaaS / PaaS based on these applications.
 * Copyright (C) 2017 Cloud Linux INC
 *
 * This file is part of KuberDock.
 *
 * KuberDock is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * KuberDock is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with KuberDock; if not, see <http://www.gnu.org/licenses/>.
 */

import App from 'isv/app';
import * as utils from 'app_data/utils';
import 'backbone-associations';
import 'backbone.paginator';
import numeral from 'numeral';

Backbone.syncOrig = Backbone.sync;
Backbone.sync = function(method, model, options){
    if (!model.noauth)  // by default all models require auth
        options.authWrap = true;
    return Backbone.syncOrig.apply(Backbone, arguments);
};

export const getParentWithType = function(model, typeOfPatent, throughCollection){
    return _.find(model.parents || [], parent => parent instanceof typeOfPatent);
};


export const CurrentUserModel = Backbone.Model.extend({
    url: App.apiUrl('users/self'),
    parse: utils.restUnwrapper,
    defaults: {
        impersonated: false
    },
    localizeDatetime(dt, formatString){
        return utils.localizeDatetime({dt, formatString, tz: this.get('timezone')});
    },
    isImpersonated(){  // TODO-JWT: get this data from token
        return this.get('impersonated');
    },
    roleIs(...roles){
        return roles.includes(this.get('rolename'));
    },
    usernameIs(...usernames){
        return usernames.includes(this.get('username'));
    }
});
App.getCurrentUser = App.resourcePromiser('user', CurrentUserModel);


// Billing & resources

export let Package, PackageCollection;
export let KubeType, KubeTypeCollection;
export let PackageKube, PackageKubeCollection;

Package = Backbone.AssociatedModel.extend({
    url(){
        return App.apiUrl(`pricing/packages/${this.id}?with_kubes=1&with_internal=1`);
    },
    parse: utils.restUnwrapper,
    defaults(){
        return {
            currency: 'USD',
            first_deposit: 0,
            id: 0,
            name: 'No name',
            period: 'month',
            prefix: '$',
            price_ip: 0,
            price_over_traffic: 0,
            price_pstorage: 0,
            suffix: ' USD',
        };
    },
    initialize(attributes, options){
        let kubes = this.get('kubes');
        this.unset('kubes');
        if (App.packageCollection == null)
            App.packageCollection = new PackageCollection();
        if (App.kubeTypeCollection == null)
            App.kubeTypeCollection = new KubeTypeCollection();
        if (App.packageKubeCollection == null)
            App.packageKubeCollection = new PackageKubeCollection();
        App.packageCollection.add(this);
        _.each(kubes, function(kube){
            App.kubeTypeCollection.add(kube);
            App.packageKubeCollection.add({package_id: this.id, kube_id: kube.id,
                kube_price: kube.price});
        }, this);
    },
    getKubeTypes() {
        let kubes = _.chain(this.parents)
            .filter(model => model instanceof PackageKube)
            .map(packageKube => packageKube.get('kubeType'))
            .value();
        return new KubeTypeCollection(kubes);
    },
    priceFor(kubeID) {
        let packageKube = _.find(this.parents, function(model){
            return model instanceof PackageKube &&
                model.get('kubeType').id === kubeID;
        });
        return packageKube ? packageKube.get('kube_price') : undefined;
    },
    getFormattedPrice(price, format) {
        return this.get('prefix') +
            numeral(price).format(format || '0.00') +
            this.get('suffix');
    },
});
PackageCollection = Backbone.Collection.extend({
    url: App.apiUrl('pricing/packages/?with_kubes=1&with_internal=1'),
    model: Package,
    parse: utils.restUnwrapper,
});
App.getPackages = App.resourcePromiser('packages', PackageCollection);

KubeType = Backbone.AssociatedModel.extend({
    defaults(){
        return {
            available: false,
            cpu: 0,
            cpu_units: 'Cores',
            disk_space: 0,
            disk_space_units: 'GB',
            id: null,
            included_traffic: 0,
            is_default: null,
            memory: 0,
            memory_units: 'MB',
            name: 'No name',
        };
    },
});
KubeType.noAvailableKubeTypes = new KubeType(
    {name: 'No available kube types', id: 'noAvailableKubeTypes'});
KubeType.noAvailableKubeTypes.notify = function(){
    utils.notifyWindow('There are no available kube types in your package.');
};
KubeType.noAvailableKubeTypes.notifyConflict = function(){
    // Case, when there are no available kube types, 'cause of conflicts with pod's PDs.
    // TODO: better message
    utils.notifyWindow('You cannot use selected Persistent Disks with any ' +
                       'available Kube Types.');
};
KubeTypeCollection = Backbone.Collection.extend({
    model: KubeType,
    comparator(kubeType) {
        return !kubeType.get('available');
    },
});

PackageKube = Backbone.AssociatedModel.extend({
    relations: [{
        type: Backbone.One,
        key: 'kubeType',
        relatedModel: KubeType,
    }, {
        type: Backbone.One,
        key: 'package',
        relatedModel: Package,
    }],
    defaults: {kube_price: 0},
    initialize(){
        this.reattach();
        this.on('change:package_id change:kube_id', this.reattach);
    },
    reattach(){
        this.set('kubeType', App.kubeTypeCollection.get(this.get('kube_id')));
        this.set('package', App.packageCollection.get(this.get('package_id')));
    },
});
PackageKubeCollection = Backbone.Collection.extend({
    model: PackageKube,
});


// Pod-related stuff

export let Pod, PodCollection, Container, AppUpdate;


Container = Backbone.AssociatedModel.extend({
    idAttribute: 'name',
    defaults(){
        return {
            image: null,
            name: _.random(Math.pow(36, 8)).toString(36),
            ports: [],
            volumeMounts: [],
            env: [],
            command: [],
            args: [],
            kubes: 1,
            terminationMessagePath: null,
        };
    },
    getPod(){ return getParentWithType(this.collection, Pod); },
    exec(command){
        return new Promise((resolve, reject) => {
            let url = App.apiUrl('podapi', this.getPod().id, this.get('name'), 'exec');
            $.ajax({url, data: {command}, authWrap: true, method: 'POST'})
                .then(response => resolve(utils.restUnwrapper(response)), reject);
        });
    },
});

Pod = Backbone.AssociatedModel.extend({
    urlRoot: App.apiUrl('podapi'),
    parse: utils.restUnwrapper,

    relations: [{
        type: Backbone.Many,
        key: 'containers',
        relatedModel: Container,
    }],

    defaults(){
        var kubeTypes = new KubeTypeCollection(
                App.userPackage.getKubeTypes().where({available: true})),
            defaultKube = kubeTypes.findWhere({is_default: true}) ||
                kubeTypes.at(0) || KubeType.noAvailableKubeTypes;
        return {
            name: 'Nameless',
            containers: [],
            volumes: [],
            appCommands: {},
            ready: false,
            replicas: 1,
            restartPolicy: 'Always',
            node: null,
            kube_type: defaultKube.id,
            status: 'stopped',
            custom_domain: null,
        };
    },

    resetPassword(){
        return new Promise((resolve, reject) => {
            const action = this.get('appCommands', {}).resetPassword;
            if (!action)
                return reject("Application doesn't support this action.");
            const container = this.get('containers')
                .findWhere({name: action.container});
            if (!container)
                return reject(`Container "${action.container}" was not found.`);
            container.exec(action.command).then(resolve, reject);
        });
    },

    resetSshAccess(){
        utils.preloader2.show();
        return new Promise(
            (resolve, reject) => {
                $.ajax({
                    url: App.apiUrl(`podapi/${this.id}/reset_direct_access_pass`),
                    authWrap: true,
                })
                .done((response) => {
                    this.set('direct_access', response.data);
                    utils.notifyWindow('SSH credentials are updated.', 'success');
                    resolve(response);
                })
                .always(utils.preloader2.hide).fail((result) => {
                    utils.notifyWindow(result);
                    reject(result);
                });
            }
        );
    },

    command(command, commandOptions = {}){
        // patch should include previous `set`
        let data = _.extend(this.changedAttributes() || {},
                            {command, commandOptions});
        return this.save(data, {wait: true, patch: true});
    },

    getPrettyStatus(){
        let status = this.get('status');
        if (status === 'running' && !this.get('ready'))
            return 'pending';
        else if (status === 'preparing')
            return 'deploying';
        return status || 'stopped';
    },

    /**
     * Check that `command` is applicable to the current sate of the pod
     * @param {string} command - name of the command
     * @returns {boolean} - whether or not it's applicable
     */
    ableTo(command){
        // 'unpaid', 'stopped', 'stopping', 'waiting', 'pending',
        // 'preparing', 'running', 'failed', 'succeeded'
        var status = this.get('status'),
            isInternalUser = App.currentUser.usernameIs('kuberdock-internal');
        if (command === 'start')
            return _.contains(['stopped'], status);
        if (command === 'restore')
            return _.contains(['paid_deleted'], status);
        if (command === 'redeploy')
            return _.contains(['stopping', 'waiting', 'pending', 'running',
                'failed', 'succeeded', 'preparing'], status);
        if (command === 'stop' || command === 'restart')
            return _.contains(['stopping', 'waiting', 'pending', 'running',
                'failed', 'succeeded', 'preparing'], status);
        if (command === 'pay-and-start')
            return _.contains(['unpaid'], status);
        if (command === 'delete')
            return _.contains(['unpaid', 'stopped', 'stopping', 'waiting',
                'running', 'failed', 'succeeded'], status) && !isInternalUser;
        if (command === 'switch-package')
            return !!(this.get('template_id') &&
                      this.get('template_plan_name') &&
                      !this.get('forbidSwitchingAppPackage'));
    },

    setCustomDomain(domain){
        utils.preloader2.show();
        return this.command('set', {'custom_domain':domain})
            .always(utils.preloader2.hide).fail(utils.notifyWindow);
    },
    checkForUpdate(){
        let container = this;
        utils.preloader2.show();
        return new AppUpdate({}, {container: container}).fetch()
            .always(utils.preloader2.hide)
            .fail(utils.notifyWindow)
            .done((rs) => {
                if (!rs)
                    utils.notifyWindow('No updates found', 'success');
                if (!rs.data.updateAvailable){
                    utils.notifyWindow('You have latest version', 'success');
                } else {
                    container.update(rs.data.activeVersionID);
                }
            });
    },
    update(version){
        let model = this;
        utils.modalDialog({
            title: 'Update avalable',
            body: `New update "${version}" is available. To update click "Update now"`,
            small: true,
            show: true,
            footer: {
                buttonOk: () => {
                    utils.preloader2.show();
                    new AppUpdate({}, {container: model}).save()
                        .done(() => {
                            model.set('template_version_id', version);
                        })
                        .always(utils.preloader2.hide).fail(utils.notifyWindow);
                },
                buttonOkText: 'Update now',
                buttonCancel: true,
            },
        });
    },
});

AppUpdate = Backbone.Model.extend({
    url() {
        return App.apiUrl(`yamlapi/update/${this.container.id}`);
    },
    initialize(attributes, options){
        this.container = options.container;
    },
});

PodCollection = Backbone.Collection.extend({
    url: App.apiUrl('podapi'),
    model: Pod,
    parse: utils.restUnwrapper,
});
App.getPodCollection = App.resourcePromiser('podCollection', PodCollection);

// Backup stuff

export let Backup, BackupCollection;

Backup = Backbone.Model.extend({
    defaults: {
        timestamp: '1970-01-01 00:00:00',
        size: '0.0GB'
    }
});

BackupCollection = Backbone.PageableCollection.extend({
    url: App.apiUrl('podapi', 'backup'),
    model: Backup,
    parse: utils.restUnwrapper,
    mode: 'client',
    state: {
        pageSize: 8,
    },
});

App.getBackupCollection = App.resourcePromiser('backupCollection', BackupCollection);
