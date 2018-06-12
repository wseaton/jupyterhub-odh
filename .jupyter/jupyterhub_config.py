c.KubeSpawner.http_timeout = 60 * 3 #Images are big, take time to pull

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
                                'command': ['bash', '-c', 'python /opt/app-root/src/public_service.py'],
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


# Work out the public server address for the OpenShift REST API. Don't
# know how to get this via the REST API client so do a raw request to
# get it. Make sure request is done in a session so connection is closed
# and later calls against REST API don't attempt to reuse it. This is
# just to avoid potential for any problems with connection reuse.

server_url = 'https://openshift.default.svc.cluster.local'
api_url = '%s/oapi' % server_url

with requests.Session() as session:
    response = session.get(api_url, verify=False)
    data = json.loads(response.content.decode('UTF-8'))
    address = data['serverAddressByClientCIDRs'][0]['serverAddress']

# Enable the OpenShift authenticator. The OPENSHIFT_URL environment
# variable must be set before importing the authenticator as it only
# reads it when module is first imported.

os.environ['OPENSHIFT_URL'] = 'https://%s' % address

from oauthenticator.openshift import OpenShiftOAuthenticator
c.JupyterHub.authenticator_class = OpenShiftOAuthenticator

# Override scope as oauthenticator code doesn't set it correctly.
# Need to lodge a PR against oauthenticator to have this fixed.

#OpenShiftOAuthenticator.scope = ['user:info']

# Setup authenticator configuration using details from environment.

service_name = os.environ['JUPYTERHUB_SERVICE_NAME']

service_account_name = '%s-hub' %  service_name
service_account_path = '/var/run/secrets/kubernetes.io/serviceaccount'

with open(os.path.join(service_account_path, 'namespace')) as fp:
    namespace = fp.read().strip()

client_id = 'system:serviceaccount:%s:%s' % (namespace, service_account_name)

c.OpenShiftOAuthenticator.client_id = client_id

with open(os.path.join(service_account_path, 'token')) as fp:
    client_secret = fp.read().strip()

c.OpenShiftOAuthenticator.client_secret = client_secret

# Work out hostname for the exposed route of the JupyterHub server. This
# is tricky as we need to use the REST API to query it.

import openshift.client
import openshift.config

openshift.config.load_incluster_config()

api_client = openshift.client.ApiClient()
oapi_client = openshift.client.OapiApi(api_client)

route_list = oapi_client.list_namespaced_route(namespace)

host = None

for route in route_list.items:
    if route.metadata.name == service_name:
        host = route.spec.host

if not host:
    raise RuntimeError('Cannot calculate external host name for JupyterHub.')

c.OpenShiftOAuthenticator.oauth_callback_url = 'https://%s/hub/oauth_callback' % host

from jupyterhub_singleuser_profiles.profiles import SingleuserProfiles

from kubespawner import KubeSpawner
class OpenShiftSpawner(KubeSpawner):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

  def _options_form_default(self):
    imagestream_list = oapi_client.list_namespaced_image_stream(namespace)

    result = []
    for i in imagestream_list.items:
      if "-notebook" in i.metadata.name:
        name = i.metadata.name
        if not i.status.tags:
            continue
        for tag in i.status.tags:
          image = "%s:%s" % (name, tag.tag)
          result.append("<option value='%s'>%s</option>" % (image, image))

    return """
    <label for="custom_image">Select desired notebook image</label>
    <select name="custom_image" size="1">
    %s
    </select>
    """ % "\n".join(result)

  def options_from_form(self, formdata):
    options = {}
    options['custom_image'] = formdata['custom_image'][0]
    self.singleuser_image_spec = options['custom_image']
    return options

def apply_pod_profile(spawner, pod):
  ss = SingleuserProfiles()
  ss.load_profiles()
  profile = ss.get_merged_profile(spawner.singleuser_image_spec, user=spawner.user.name)
  return SingleuserProfiles.apply_pod_profile(spawner, pod, profile)

c.JupyterHub.spawner_class = OpenShiftSpawner

c.OpenShiftSpawner.modify_pod_hook = apply_pod_profile
c.OpenShiftSpawner.cpu_limit = float(os.environ.get("SINGLEUSER_CPU_LIMIT", "1"))
c.OpenShiftSpawner.mem_limit = os.environ.get("SINGLEUSER_MEM_LIMIT", "1G")
c.OpenShiftSpawner.user_storage_pvc_ensure = True
c.KubeSpawner.user_storage_capacity = '2Gi'
c.KubeSpawner.pvc_name_template = '%s-nb-{username}-pvc' % c.KubeSpawner.hub_connect_ip
c.KubeSpawner.volumes = [dict(name='data', persistentVolumeClaim=dict(claimName=c.KubeSpawner.pvc_name_template))]
c.KubeSpawner.volume_mounts = [dict(name='data', mountPath='/opt/app-root/src')]
c.KubeSpawner.user_storage_class = os.environ.get("JUPYTERHUB_STORAGE_CLASS", c.KubeSpawner.user_storage_class)
