

build: 
	s2i build . quay.io/odh-jupyterhub/jupyterhub:v3.5.1 jupyterhub-odh:ldap
	docker image tag jupyterhub-odh:ldap quay.io/wseaton/jupyterhub-odh:ldap
	docker push quay.io/wseaton/jupyterhub-odh:ldap