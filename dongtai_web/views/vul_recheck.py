#!/usr/bin/env python
# -*- coding:utf-8 -*-
# author:owefsad
# software: PyCharm
# project: lingzhi-webapi
import logging

import time
from dongtai_common.models.agent import IastAgent
from dongtai_common.models.project import IastProject
from dongtai_common.models.replay_queue import IastReplayQueue
from dongtai_common.models.vulnerablity import IastVulnerabilityModel
from dongtai_common.utils.validate import Validate
from dongtai_web.utils import extend_schema_with_envcheck, get_response_serializer
from rest_framework import serializers

from dongtai_common.endpoint import R
from dongtai_common.utils import const
from dongtai_common.endpoint import UserEndPoint
from django.utils.translation import gettext_lazy as _
from django.db.models import F
from django.db.models import Q
import threading

from dongtai_web.vul_log.vul_log import log_recheck_vul

logger = logging.getLogger('dongtai-webapi')


class VulReCheckDataSerializer(serializers.Serializer):
    no_agent = serializers.BooleanField(
        help_text=_('Whether the project does not exist agent'))
    pending = serializers.IntegerField(
        help_text=_('Waiting queue length for replay'))
    recheck = serializers.IntegerField(
        help_text=_('Success queue length for replay'))
    checking = serializers.IntegerField(
        help_text=_('Checking queue length for replay'))


_ResponseGetSerializer = get_response_serializer(
    VulReCheckDataSerializer(),
    status_msg_keypair=(
        ((201, _('Handle success')), ''),
        ((202, _('Item ID should not be empty')), ''),
        ((202, _('Incorrect format parameter')), ''),
        ((202, _('Batch playback error')), ''),
        ((202, _('Current application has not been associated with probes and cannot be reproduced.')), ''),
        ((202, _('No permission to access')), ''),
    ))
_ResponsePostSerializer = get_response_serializer(
    VulReCheckDataSerializer(),
    status_msg_keypair=(
        ((201, _('Handle success')), ''),
        ((202, _('IDS should not be empty')), ''),
        ((202, _('IDS must be: Vulnerability ID, Vulnerability ID Format')), ''),
        ((202, _('Vulnerability replay error')), ''),
    ))


class VulReCheck(UserEndPoint):
    @staticmethod
    def recheck(vul_queryset):
        timestamp = int(time.time())
        waiting_count = 0
        success_count = 0
        re_success_count = 0
        opt_vul_queryset = vul_queryset.only('agent__id', 'id')
        vul_ids = [i.id for i in opt_vul_queryset]
        vul_id_agentmap = {i.id:i.agent_id for i in opt_vul_queryset}
        history_replay_vul_ids = IastReplayQueue.objects.filter(
            relation_id__in=vul_ids,
            replay_type=const.VUL_REPLAY).order_by('relation_id').values_list(
                'relation_id', flat=True).distinct()
        waiting_count = IastReplayQueue.objects.filter(
            Q(relation_id__in=vul_ids)
            & Q(replay_type=const.VUL_REPLAY)
            & Q(state__in=(const.PENDING, const.WAITING))).count()
        re_success_count = IastReplayQueue.objects.filter(
            Q(relation_id__in=[i.id for i in opt_vul_queryset])
            & Q(replay_type=const.VUL_REPLAY)
            & ~Q(state__in=(const.PENDING, const.WAITING))).update(
                state=const.WAITING,
                count=F('count') + 1,
                update_time=timestamp)
        vuls_not_exist = set(vul_ids) - set(history_replay_vul_ids)
        success_count = len(vuls_not_exist)
        IastReplayQueue.objects.bulk_create(
            [
                IastReplayQueue(agent_id=vul_id_agentmap[vul_id],
                                relation_id=vul_id,
                                state=const.WAITING,
                                count=1,
                                create_time=timestamp,
                                update_time=timestamp,
                                replay_type=const.VUL_REPLAY)
                for vul_id in vuls_not_exist
            ],
            ignore_conflicts=True)
        vul_queryset.update(status_id=1, latest_time=timestamp)
        return waiting_count, success_count, re_success_count

    @staticmethod
    def vul_check_for_queryset(vul_queryset):
        active_agent_ids = IastAgent.objects.filter(
            id__in=vul_queryset.values('agent_id'),
            online=const.RUNNING,
            is_core_running=const.CORE_IS_RUNNING).values(
                "id").distinct().all()
        no_agent = vul_queryset.filter(~Q(
            agent_id__in=active_agent_ids)).count()
        waiting_count, success_count, re_success_count = VulReCheck.recheck(
            vul_queryset)
        return no_agent, waiting_count, success_count, re_success_count

    @extend_schema_with_envcheck(
        [{
            'name':
            'type',
            'type':
            str,
            'description':
            _('''available options are ("all","project").
                Corresponding to all or specific project respectively.''')
        }, {
            'name':
            "projectId",
            'type':
            int,
            'description':
            _("""The corresponding id of the Project.
            Only If the type is project, the projectId here will be used.""")
        }],
        tags=[_('Vulnerability')],
        summary=_("Vulnerability verification"),
        description=_("""Verify the user's corresponding vulnerabilities.
            Need to specify the type"""),
        response_schema=_ResponsePostSerializer
    )
    def post(self, request):
        """
        :param request:
        :return:
        """
        try:
            vul_ids = request.data.get('ids')
            if vul_ids is None or vul_ids == '':
                return R.failure(_("IDS should not be empty"))

            vul_ids = vul_ids.split(',')
            if Validate.is_number(vul_ids) is False:
                return R.failure(_('IDS must be: Vulnerability ID, Vulnerability ID Format'))

            auth_agents = self.get_auth_agents_with_user(user=request.user)
            vul_queryset = IastVulnerabilityModel.objects.filter(
                id__in=vul_ids, agent__in=auth_agents)
            no_agent, waiting_count, success_count, re_success_count = self.vul_check_for_queryset(
                vul_queryset)
            # 加重放日志 vul_ids
            log_recheck_vul(request.user.id,request.user.username,vul_ids,"待验证")

            # def log_recheck_vul(user_id: int, user_name: str, vul_id: list,  vul_status: str):
            return R.success(data={
                "no_agent": no_agent,
                "pending": waiting_count,
                "recheck": re_success_count,
                "checking": success_count
            },msg=_('Handle success'))

        except Exception as e:
            logger.error(f' msg:{e}')
            return R.failure(msg=_('Vulnerability replay error'))

    def vul_check_for_project(self, project_id, auth_users):
        try:
            project_exist = IastProject.objects.values("id").filter(
                id=project_id, user__in=auth_users).exists()
            if project_exist:
                agent_queryset = IastAgent.objects.values("id").filter(
                    bind_project_id=project_id)
                if agent_queryset:
                    agent_ids = agent_queryset.values_list('id',flat=True)
                    vul_queryset = IastVulnerabilityModel.objects.filter(
                        agent_id__in=agent_ids)
                    waiting_count, success_count, re_success_count = self.recheck(
                        vul_queryset)
                    return True, waiting_count, re_success_count, success_count, None
                else:
                    return False, 0, 0, 0, _(
                        'Current application has not been associated with probes and cannot be reproduced.'
                    )
            else:
                return False, 0, 0, 0, _('No permission to access')
        except Exception as e:
            logger.error(f' msg:{e}',exc_info=True)
            return False, 0, 0, 0, _('Batch playback error')

    @extend_schema_with_envcheck(
        [{
            'name':
            'type',
            'type':
            str,
            'description':
            _('''available options are ("all","project").
                Corresponding to all or specific project respectively.''')
        }, {
            'name':
            "projectId",
            'type':
            int,
            'description':
            _("""The corresponding id of the Project.
            Only If the type is project, the projectId here will be used.""")
        }],
        tags=[_('Vulnerability')],
        summary=_("Vulnerability verification"),
        description=_("""Verify the user's corresponding vulnerabilities.
            Need to specify the type"""),
        response_schema=_ResponsePostSerializer
    )
    def get(self, request):

        try:
            check_type = request.query_params.get('type')
            project_id = request.query_params.get('projectId')
            if check_type == 'project' and not project_id:
                return R.failure(msg=_("Item ID should not be empty"))
            if check_type == 'all':
                vul_queryset = IastVulnerabilityModel.objects.filter(
                    agent__in=self.get_auth_agents_with_user(request.user))
                no_agent, pending, recheck, checking = self.vul_check_for_queryset(
                    vul_queryset)

            elif check_type == 'project':
                auth_users = self.get_auth_users(request.user)
                status, pending, recheck, checking, msg = self.vul_check_for_project(
                    project_id, auth_users=auth_users)
                no_agent = None
            return R.success(data={
                "no_agent": no_agent if no_agent else 0,
                "pending": pending,
                "recheck": recheck,
                "checking": checking
            },
                             msg=_("Handle success"))
            return R.failure(msg=_("Incorrect format parameter"))

        except Exception as e:
            logger.error(f'user_id:{request.user.id} msg:{e}', exc_info=True)
            return R.failure(msg=_('Batch playback error'))
