#!/usr/bin/env python
# -*- coding:utf-8 -*-
# author:owefsad
# datetime:2020/6/3 11:36
# software: PyCharm
# project: webapi
from dongtai.endpoint import UserEndPoint, R
from dongtai.models.deploy import IastDeployDesc
from django.utils.translation import gettext_lazy as _


class AgentDeployDesc(UserEndPoint):
    name = "api-v1-iast-deploy-desc"
    description = _("Agent deployment document")

    def get(self, request):
        queryset = IastDeployDesc.objects.all()

        os = request.query_params.get('os', 'linux')
        if os:
            queryset = queryset.filter(os=os)

        middle = request.query_params.get('server', 'tomcat')
        if middle:
            queryset = queryset.filter(middleware=middle)

        queryset = queryset.last()
        if queryset:
            return R.success(msg=queryset.desc)
        else:
            return R.failure(msg=_('No data'))
