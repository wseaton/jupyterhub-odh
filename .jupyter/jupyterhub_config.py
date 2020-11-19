c.KubeSpawner.http_timeout = 60 * 10 #Images are big, take time to pull, make it 10 mins for now because of storage issue
c.KubeSpawner.start_timeout = 60 * 10 #Images are big, take time to pull, make it 10 mins for now because of storage issue

import os

import json
import requests

c.JupyterHub.log_level = 'DEBUG'
# Do not shut down singleuser servers on restart
c.JupyterHub.cleanup_servers = False


import uuid
c.ConfigurableHTTPProxy.auth_token = str(uuid.uuid4())
public_service_dict = {
                        'PROXY_TOKEN': c.ConfigurableHTTPProxy.auth_token,
                        'PROXY_API_URL': 'http://%s:%d/' % ("127.0.0.1", 8082)
                    }
public_service_dict.update(os.environ)
c.JupyterHub.services = [
                            {
                                'name': 'public',
                                'command': ['bash', '-c', 'jupyter_publish_service'],
                                'environment': public_service_dict
                            }
                        ]
c.KubeSpawner.singleuser_extra_containers = [
        {
            "name": "nbviewer",
            "image": "nbviewer:latest",
            "ports": [
                {
                    "containerPort": 9090,
                    "protocol": "TCP"
                }
            ],
            "env" : [
                {
                    "name": "NBVIEWER_LOCALFILES",
                    "value": "/opt/app-root/src/public_notebooks"
                },
                {
                    "name": "NBVIEWER_TEMPLATES",
                    "value": "/opt/app-root/src"
                },
                {
                    "name": "NBVIEWER_PORT",
                    "value": "9090"
                },
                {
                    "name": "JUPYTERHUB_SERVICE_PREFIX",
                    "value": "/user/{username}/public/"
                },
                {
                    "name": "CACHE_EXPIRY_MIN",
                    "value": "30"
                },
                {
                    "name": "CACHE_EXPIRY_MAX",
                    "value": "60"
                },
                {
                    "name": "NO_CACHE",
                    "value": "true"
                }
            ],
        "volumeMounts": [
            {
                "mountPath": "/opt/app-root/src",
                "name": "data"
            }
        ]
        }
    ]


from traitlets import Unicode
from jupyterhub.auth import Authenticator
from tornado import gen

class DummyDataAuthenticator(Authenticator):
    password = Unicode(
        None,
        allow_none=True,
        config=True,
        help="""
        Set a global password for all users wanting to log in.
        This allows users with any username to log in with the same static password.
        """
    )

    @gen.coroutine
    def authenticate(self, handler, data):
        if self.password:
            if data['password'] == self.password:
                return data['username']
            return None

        # super_secret = yield secret_for_user(username)

        return {
            'name': data['username'],
            'auth_state': {
                'super_secret': "MYSUPERDUPERSECRET", # could be retrieved from vault
            },
        }


    @gen.coroutine
    def pre_spawn_start(self, user, spawner):
        """Pass upstream_token to spawner via environment variable"""
        auth_state = yield user.get_auth_state()
        if not auth_state:
            # auth_state not enabled
            return
        spawner.environment['SUPER_SECRET'] = auth_state['super_secret']



c.JupyterHub.authenticator_class = DummyDataAuthenticator
c.DummyDataAuthenticator.enable_auth_state = True


if 'JUPYTERHUB_CRYPT_KEY' not in os.environ:
    warnings.warn(
        "Need JUPYTERHUB_CRYPT_KEY env for persistent auth_state.\n"
        "    export JUPYTERHUB_CRYPT_KEY=$(openssl rand -hex 32)"
    )
    c.CryptKeeper.keys = [ os.urandom(32) ]


from jupyterhub_singleuser_profiles.profiles import SingleuserProfiles

from kubespawner import KubeSpawner
class OpenShiftSpawner(KubeSpawner):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.single_user_services = []
    self.single_user_profiles = SingleuserProfiles(gpu_mode=os.environ.get('GPU_MODE'), verify_ssl=verify_ssl)
    self.gpu_mode = self.single_user_profiles.gpu_mode
    self.gpu_count = 0
    self.deployment_size = None

  def _options_form_default(self):
    cm_data = self.single_user_profiles.get_user_profile_cm(self.user.name)
    envs = cm_data.get('env', {})
    gpu = cm_data.get('gpu', 0)
    last_image = cm_data.get('last_selected_image', '')
    last_size = cm_data.get('last_selected_size', '')
    
    response = self.single_user_profiles.get_image_list_form(self.user.name)

    response += self.single_user_profiles.get_sizes_form(self.user.name)

    response += """
        <p>
            <label>GPU: </label>
            <input class="form-control" type="text" value="%s" name="gpu" />
        </p>
        """ % gpu

    aws_env = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY']
    for key in aws_env:
        if key not in envs.keys():
            envs[key] = ""

    response += "<h3>Environment Variables</h3>"
    for key, val in envs.items():
        input_type = "text"
        if key in aws_env:
            input_type = "password"
        response += """
        <p>
        <label>%s: </label><input class="form-control" type="%s" value="%s" name="%s" />
        </p>
        """ % (key, input_type, val, key)

    response += """
    <p>
    <label>Variable name: </label><input class="form-control" type="text" name="variable_name_1" />
    <label>Variable value: </label><input class="form-control" type="text" name="variable_value_1" />
    </p>
    """

    return response

  def options_from_form(self, formdata):
    options = {}
    options['custom_image'] = formdata['custom_image'][0]
    options['size'] = formdata['size'][0]
    self.image_spec = options['custom_image']
    self.deployment_size = formdata['size'][0]
    del formdata['custom_image']
    del formdata['size']

    self.gpu_count = formdata['gpu'][0]
    del formdata['gpu']

    data = {} #'AWS_ACCESS_KEY_ID': formdata['AWS_ACCESS_KEY_ID'][0], 'AWS_SECRET_ACCESS_KEY': formdata['AWS_SECRET_ACCESS_KEY'][0]
    for key, val in formdata.items():
        if key.startswith("variable_name"):
            index = key.split("_")[-1]
            if len(formdata[key][0]) > 0:
                data[formdata[key][0]] = formdata['variable_value_%s' % index][0]
        elif not key.startswith("variable_value") and len(formdata[key][0]) > 0:
            data[key] = formdata[key][0]

    cm_data = {
        'env': data,
        'last_selected_image': self.singleuser_image_spec,
        'gpu': self.gpu_count,
        'last_selected_size': self.deployment_size
        }

    self.single_user_profiles.update_user_profile_cm(self.user.name, cm_data)
    return options



def apply_pod_profile(spawner, pod):
  spawner.single_user_profiles.load_profiles(username=spawner.user.name)
  profile = spawner.single_user_profiles.get_merged_profile(spawner.image_spec, user=spawner.user.name, size=spawner.deployment_size)
  return SingleuserProfiles.apply_pod_profile(spawner, pod, profile)

def setup_environment(spawner):
    spawner.single_user_profiles.load_profiles(username=spawner.user.name)
    spawner.single_user_profiles.setup_services(spawner, spawner.image_spec, spawner.user.name)

def clean_environment(spawner):
    spawner.single_user_profiles.clean_services(spawner, spawner.user.name)

c.JupyterHub.spawner_class = OpenShiftSpawner

c.OpenShiftSpawner.pre_spawn_hook = setup_environment
c.OpenShiftSpawner.post_stop_hook = clean_environment
c.OpenShiftSpawner.modify_pod_hook = apply_pod_profile
c.OpenShiftSpawner.cpu_limit = float(os.environ.get("SINGLEUSER_CPU_LIMIT", "1"))
c.OpenShiftSpawner.mem_limit = os.environ.get("SINGLEUSER_MEM_LIMIT", "1G")
c.OpenShiftSpawner.storage_pvc_ensure = True
c.KubeSpawner.storage_capacity = os.environ.get('SINGLEUSER_PVC_SIZE', '2Gi')
c.KubeSpawner.pvc_name_template = '%s-nb-{username}-pvc' % os.environ['JUPYTERHUB_SERVICE_NAME']
c.KubeSpawner.volumes = [dict(name='data', persistentVolumeClaim=dict(claimName=c.KubeSpawner.pvc_name_template))]
c.KubeSpawner.volume_mounts = [dict(name='data', mountPath='/opt/app-root/src')]
c.KubeSpawner.user_storage_class = os.environ.get("JUPYTERHUB_STORAGE_CLASS", c.KubeSpawner.user_storage_class)
admin_users = os.environ.get('JUPYTERHUB_ADMIN_USERS')
if admin_users:
    c.Authenticator.admin_users = set(admin_users.split(','))
