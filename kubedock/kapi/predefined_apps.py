from ..utils import APIError
from ..predefined_apps.models import PredefinedApp as PredefinedAppModel


class PredefinedApps(object):
    def __init__(self, user=None):
        if user is not None and not isinstance(user, int):
            user = user.id
        self.user = user

        self.apps = PredefinedAppModel.query
        if self.user is not None:
            self.apps = self.apps.filter_by(user_id=self.user)

    def get(self, app_id=None):
        if app_id is None:
            return [app.to_dict() for app in self.apps]

        app = self.apps.filter_by(id=app_id).first()
        if app is None:
            raise APIError('Not found', status_code=404)
        return app.to_dict()

    def create(self, template):
        if template is None:
            raise APIError('Template not specified')
        app = PredefinedAppModel(user_id=self.user, template=template).save()
        return app.to_dict()

    def update(self, app_id, template):
        app = self.apps.filter_by(id=app_id).first()
        if app is None:
            raise APIError('Not found', status_code=404)
        app.template = template
        app.save()
        return app.to_dict()

    def delete(self, app_id):
        app = self.apps.filter_by(id=app_id).first()
        if app is None:
            raise APIError('Not found', status_code=404)
        app.delete()