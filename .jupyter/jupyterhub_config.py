c.KubeSpawner.http_timeout = (
    60 * 10
)  # Images are big, take time to pull, make it 10 mins for now because of storage issue
c.KubeSpawner.start_timeout = (
    60 * 10
)  # Images are big, take time to pull, make it 10 mins for now because of storage issue

import json
import os

import requests

c.JupyterHub.log_level = "DEBUG"
# Do not shut down singleuser servers on restart
c.JupyterHub.cleanup_servers = False


import uuid

c.ConfigurableHTTPProxy.auth_token = str(uuid.uuid4())
public_service_dict = {
    "PROXY_TOKEN": c.ConfigurableHTTPProxy.auth_token,
    "PROXY_API_URL": "http://%s:%d/" % ("127.0.0.1", 8082),
}
public_service_dict.update(os.environ)
c.JupyterHub.services = [
    {
        "name": "public",
        "command": ["bash", "-c", "jupyter_publish_service"],
        "environment": public_service_dict,
    }
]
c.KubeSpawner.singleuser_extra_containers = [
    {
        "name": "nbviewer",
        "image": "nbviewer:latest",
        "ports": [{"containerPort": 9090, "protocol": "TCP"}],
        "env": [
            {
                "name": "NBVIEWER_LOCALFILES",
                "value": "/opt/app-root/src/public_notebooks",
            },
            {"name": "NBVIEWER_TEMPLATES", "value": "/opt/app-root/src"},
            {"name": "NBVIEWER_PORT", "value": "9090"},
            {"name": "JUPYTERHUB_SERVICE_PREFIX", "value": "/user/{username}/public/"},
            {"name": "CACHE_EXPIRY_MIN", "value": "30"},
            {"name": "CACHE_EXPIRY_MAX", "value": "60"},
            {"name": "NO_CACHE", "value": "true"},
        ],
        "volumeMounts": [{"mountPath": "/opt/app-root/src", "name": "data"}],
    }
]


import warnings

from jupyterhub.auth import Authenticator
from tornado import gen
from traitlets import Unicode

if "JUPYTERHUB_CRYPT_KEY" not in os.environ:
    warnings.warn(
        "Need JUPYTERHUB_CRYPT_KEY env for persistent auth_state.\n"
        "    export JUPYTERHUB_CRYPT_KEY=$(openssl rand -hex 32)"
    )
    c.CryptKeeper.keys = [os.urandom(32)]


from jupyterhub_singleuser_profiles.profiles import SingleuserProfiles
from kubespawner import KubeSpawner


class OpenShiftSpawner(KubeSpawner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.single_user_services = []
        self.single_user_profiles = SingleuserProfiles(gpu_mode=os.environ.get("GPU_MODE"))
        self.gpu_mode = self.single_user_profiles.gpu_mode
        self.gpu_count = 0
        self.deployment_size = None

    def _options_form_default(self):
        cm_data = self.single_user_profiles.get_user_profile_cm(self.user.name)
        envs = cm_data.get("env", {})
        gpu = cm_data.get("gpu", 0)
        last_image = cm_data.get("last_selected_image", "")
        last_size = cm_data.get("last_selected_size", "")

        response = self.single_user_profiles.get_image_list_form(self.user.name)

        response += self.single_user_profiles.get_sizes_form(self.user.name)

        response += (
            """
        <p>
            <label>GPU: </label>
            <input class="form-control" type="text" value="%s" name="gpu" />
        </p>
        """
            % gpu
        )

        aws_env = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
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
        """ % (
                key,
                input_type,
                val,
                key,
            )

        response += """
    <p>
    <label>Variable name: </label><input class="form-control" type="text" name="variable_name_1" />
    <label>Variable value: </label><input class="form-control" type="text" name="variable_value_1" />
    </p>
    """

        return response

    def options_from_form(self, formdata):
        options = {}
        options["custom_image"] = formdata["custom_image"][0]
        options["size"] = formdata["size"][0]
        self.image_spec = options["custom_image"]
        self.deployment_size = formdata["size"][0]
        del formdata["custom_image"]
        del formdata["size"]

        self.gpu_count = formdata["gpu"][0]
        del formdata["gpu"]

        data = (
            {}
        )  #'AWS_ACCESS_KEY_ID': formdata['AWS_ACCESS_KEY_ID'][0], 'AWS_SECRET_ACCESS_KEY': formdata['AWS_SECRET_ACCESS_KEY'][0]
        for key, val in formdata.items():
            if key.startswith("variable_name"):
                index = key.split("_")[-1]
                if len(formdata[key][0]) > 0:
                    data[formdata[key][0]] = formdata["variable_value_%s" % index][0]
            elif not key.startswith("variable_value") and len(formdata[key][0]) > 0:
                data[key] = formdata[key][0]

        cm_data = {
            "env": data,
            "last_selected_image": self.singleuser_image_spec,
            "gpu": self.gpu_count,
            "last_selected_size": self.deployment_size,
        }

        self.single_user_profiles.update_user_profile_cm(self.user.name, cm_data)
        return options





def setup_environment(spawner):
    spawner.single_user_profiles.load_profiles(username=spawner.user.name)
    spawner.single_user_profiles.setup_services(
        spawner, spawner.image_spec, spawner.user.name
    )


def clean_environment(spawner):
    spawner.single_user_profiles.clean_services(spawner, spawner.user.name)


c.JupyterHub.spawner_class = OpenShiftSpawner

c.OpenShiftSpawner.pre_spawn_hook = setup_environment
c.OpenShiftSpawner.post_stop_hook = clean_environment

c.OpenShiftSpawner.cpu_limit = float(os.environ.get("SINGLEUSER_CPU_LIMIT", "1"))
c.OpenShiftSpawner.mem_limit = os.environ.get("SINGLEUSER_MEM_LIMIT", "1G")
c.OpenShiftSpawner.storage_pvc_ensure = True
c.KubeSpawner.storage_capacity = os.environ.get("SINGLEUSER_PVC_SIZE", "2Gi")
c.KubeSpawner.pvc_name_template = (
    "%s-nb-{username}-pvc" % os.environ["JUPYTERHUB_SERVICE_NAME"]
)
c.KubeSpawner.volumes = [
    dict(
        name="data",
        persistentVolumeClaim=dict(claimName=c.KubeSpawner.pvc_name_template),
    )
]
c.KubeSpawner.volume_mounts = [dict(name="data", mountPath="/opt/app-root/src")]
c.KubeSpawner.user_storage_class = os.environ.get(
    "JUPYTERHUB_STORAGE_CLASS", c.KubeSpawner.user_storage_class
)

import os
c.JupyterHub.authenticator_class = 'ldapauthenticator.LDAPAuthenticator'
c.LDAPAuthenticator.server_address = os.environ.get('LDAP_HOST')
c.LDAPAuthenticator.bind_dn_template = [
  "uid={username},ou=users,dc=%s,dc=com" % os.environ.get("LDAP_BIND_DN_DC")
]
c.LDAPAuthenticator.lookup_dn_search_user = os.environ.get('LDAP_USER')
c.LDAPAuthenticator.lookup_dn_search_password = os.environ.get('LDAP_PASSWORD')

allowed_groups = os.environ.get("LDAP_ALLOWED_GROUPS")  
if allowed_groups:
    c.LDAPAuthenticator.allowed_groups = set(allowed_groups.split("|"))

admin_users = os.environ.get("JUPYTERHUB_ADMIN_USERS")
if admin_users:
    c.Authenticator.admin_users = set(admin_users.split(","))

ldap_ssl = lower(os.environ.get("LDAP_USE_SSL", "")) == "true"
if ldap_ssl:
    c.LDAPAuthenticator.use_ssl = True
    c.LDAPAuthenticator.server_port = 636

if lower(os.environ.get("JUPYTERHUB_AUTH_STATE", "")) == "true":
    c.Authenticator.enable_auth_state = True
    # ex. ["memberOf"] will grab groups in OpenLDAP
    # will fail if attr unset, because there's no point in enabling auth state otherwise
    c.LDAPAuthenticator.auth_state_attributes = set(os.environ["LDAP_AUTH_STATE_ATTR"].split(","))

def auth_state_hook(spawner, auth_state):
    spawner.userdata = auth_state
c.Spawner.auth_state_hook = auth_state_hook
