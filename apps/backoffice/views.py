"""Admin portal endpoints (Part 5, sections 2, 3.2 and 8 to 11), plus B1 and B7."""

from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.backoffice import (
    catalog,
    content,
    dashboards,
    physicians,
    platform_settings,
    privacy,
    questionnaires,
    reference,
    reports,
)
from apps.backoffice import serializers as s
from apps.clinical.models import InvitationStatus, PhysicianInvitation
from apps.consultations.models import PhysicianVerificationStatus
from apps.core.permissions import IsPatient
from apps.staff import accounts
from apps.staff.api import (
    PortalSchema,
    doc,
    each,
    page,
    params,
    query_params,
    user_of,
    validated,
)
from apps.staff.models import AccountStatus, StaffType
from apps.staff.permissions import IsAdminRole

TAG = "admin"


class AdminView(APIView):
    schema = PortalSchema()
    permission_classes = [IsAdminRole]


def _created(body: dict[str, Any]) -> Response:
    return Response(body, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Staff (2.1)
# ---------------------------------------------------------------------------

STAFF_FILTERS = {
    "search": "str",
    "staff_type": tuple(StaffType.values),
    "status": tuple(AccountStatus.values),
}


class AdminStaffListView(AdminView):
    @doc(
        TAG,
        "Staff accounts",
        parameters=query_params([*STAFF_FILTERS, "page", "page_size"]),
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request: Request) -> Response:
        query = params(request, STAFF_FILTERS)
        queryset = accounts.list_staff(
            search=query.get("search", ""),
            staff_type=query.get("staff_type", ""),
            status=query.get("status", ""),
        )
        return page(request, self, queryset, each(accounts.member_payload))

    @doc(
        TAG,
        "Invite a staff member",
        request=s.StaffCreateSerializer,
        responses={201: s.StaffMemberSerializer},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.StaffCreateSerializer, request)
        user = accounts.create_staff(data=data, admin=user_of(request), request=request)
        return _created(accounts.member_payload(accounts.get_member(user.pk)))


class AdminStaffDetailView(AdminView):
    @doc(TAG, "Staff member", responses={200: s.StaffMemberSerializer})
    def get(self, request: Request, member_id: str) -> Response:
        return Response(accounts.member_payload(accounts.get_member(member_id)))

    @doc(
        TAG,
        "Update a staff member",
        request=s.StaffUpdateSerializer,
        responses={200: s.StaffMemberSerializer},
    )
    def patch(self, request: Request, member_id: str) -> Response:
        data = validated(s.StaffUpdateSerializer, request)
        user = accounts.update_staff(
            user=accounts.get_member(member_id), data=data, admin=user_of(request), request=request
        )
        return Response(accounts.member_payload(accounts.get_member(user.pk)))

    @doc(TAG, "Revoke an invited staff account", responses={204: None})
    def delete(self, request: Request, member_id: str) -> Response:
        accounts.delete_invited(
            user=accounts.get_member(member_id), admin=user_of(request), request=request
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminStaffSuspendView(AdminView):
    @doc(
        TAG,
        "Suspend a staff member",
        request=s.StaffReasonSerializer,
        responses={200: s.StaffMemberSerializer},
    )
    def post(self, request: Request, member_id: str) -> Response:
        data = validated(s.StaffReasonSerializer, request)
        user = accounts.suspend_staff(
            user=accounts.get_member(member_id),
            reason=data["reason"],
            admin=user_of(request),
            request=request,
        )
        return Response(accounts.member_payload(accounts.get_member(user.pk)))


class AdminStaffReactivateView(AdminView):
    @doc(TAG, "Reactivate a staff member", responses={200: s.StaffMemberSerializer})
    def post(self, request: Request, member_id: str) -> Response:
        user = accounts.reactivate_staff(
            user=accounts.get_member(member_id), admin=user_of(request), request=request
        )
        return Response(accounts.member_payload(accounts.get_member(user.pk)))


class AdminStaffResendView(AdminView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "staff_invitation_resend"

    @doc(TAG, "Resend a staff invitation", responses={200: s.StaffMemberSerializer})
    def post(self, request: Request, member_id: str) -> Response:
        user = accounts.resend_invitation(
            user=accounts.get_member(member_id), admin=user_of(request), request=request
        )
        return Response(accounts.member_payload(accounts.get_member(user.pk)))


# ---------------------------------------------------------------------------
# Physicians (2.3)
# ---------------------------------------------------------------------------

PHYSICIAN_FILTERS = {
    "search": "str",
    "status": (*PhysicianVerificationStatus.values, "INVITED"),
    "specialty_id": "uuid",
}


class AdminPhysicianListView(AdminView):
    @doc(TAG, "Physician cards", parameters=query_params([*PHYSICIAN_FILTERS, "page", "page_size"]))
    def get(self, request: Request) -> Response:
        query = params(request, PHYSICIAN_FILTERS)
        queryset = physicians.list_physicians(
            search=query.get("search", ""),
            status=query.get("status", ""),
            specialty_id=str(query.get("specialty_id") or ""),
        )
        return page(request, self, queryset, each(physicians.card_payload))


class AdminPhysicianDetailView(AdminView):
    @doc(TAG, "Physician detail with credentials")
    def get(self, request: Request, physician_id: str) -> Response:
        return Response(physicians.detail_payload(physicians.get_physician(physician_id)))


class AdminPhysicianVerifyView(AdminView):
    @doc(TAG, "Verify physician credentials", request=s.PhysicianVerifySerializer)
    def post(self, request: Request, physician_id: str) -> Response:
        data = validated(s.PhysicianVerifySerializer, request)
        user = physicians.get_physician(physician_id)
        physicians.verify(
            user=user, note=data.get("note", ""), admin=user_of(request), request=request
        )
        return Response(physicians.detail_payload(physicians.get_physician(physician_id)))


class AdminPhysicianRejectView(AdminView):
    @doc(TAG, "Reject physician credentials", request=s.StaffReasonSerializer)
    def post(self, request: Request, physician_id: str) -> Response:
        data = validated(s.StaffReasonSerializer, request)
        user = physicians.get_physician(physician_id)
        physicians.reject(user=user, reason=data["reason"], admin=user_of(request), request=request)
        return Response(physicians.detail_payload(physicians.get_physician(physician_id)))


class AdminPhysicianSuspendView(AdminView):
    @doc(TAG, "Suspend a physician", request=s.PhysicianSuspendSerializer)
    def post(self, request: Request, physician_id: str) -> Response:
        data = validated(s.PhysicianSuspendSerializer, request)
        user = physicians.get_physician(physician_id)
        physicians.suspend(
            user=user,
            reason=data["reason"],
            reassign_to=data.get("reassign_to_physician_id"),
            admin=user_of(request),
            request=request,
        )
        return Response(physicians.detail_payload(physicians.get_physician(physician_id)))


class AdminPhysicianReactivateView(AdminView):
    @doc(TAG, "Reactivate a suspended physician")
    def post(self, request: Request, physician_id: str) -> Response:
        user = physicians.get_physician(physician_id)
        physicians.reactivate(user=user, admin=user_of(request), request=request)
        return Response(physicians.detail_payload(physicians.get_physician(physician_id)))


class AdminPhysicianCredentialsView(AdminView):
    @doc(TAG, "Change physician credentials", request=s.CredentialsSerializer)
    def patch(self, request: Request, physician_id: str) -> Response:
        data = validated(s.CredentialsSerializer, request)
        user = physicians.get_physician(physician_id)
        physicians.update_credentials(user=user, data=data, admin=user_of(request), request=request)
        return Response(physicians.detail_payload(physicians.get_physician(physician_id)))


class AdminPhysicianInvitationListView(AdminView):
    @doc(TAG, "Physician invitations", parameters=query_params(["status", "page", "page_size"]))
    def get(self, request: Request) -> Response:
        query = params(request, {"status": tuple(InvitationStatus.values)})
        queryset = PhysicianInvitation.objects.order_by("-created_at")
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        return page(request, self, queryset, each(physicians.invitation_payload))

    @doc(
        TAG,
        "Invite a physician",
        request=s.PhysicianInviteSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.PhysicianInviteSerializer, request)
        invitation = physicians.invite(email=data["email"], admin=user_of(request), request=request)
        return _created(physicians.invitation_payload(invitation))


class AdminPhysicianInvitationResendView(AdminView):
    @doc(TAG, "Resend a physician invitation")
    def post(self, request: Request, invitation_id: str) -> Response:
        invitation = physicians.resend(
            invitation_id=invitation_id, admin=user_of(request), request=request
        )
        return Response(physicians.invitation_payload(invitation))


class AdminPhysicianInvitationRevokeView(AdminView):
    @doc(TAG, "Revoke a physician invitation")
    def post(self, request: Request, invitation_id: str) -> Response:
        invitation = physicians.revoke(
            invitation_id=invitation_id, admin=user_of(request), request=request
        )
        return Response(physicians.invitation_payload(invitation))


# ---------------------------------------------------------------------------
# Dashboard (3.2)
# ---------------------------------------------------------------------------


class AdminDashboardView(AdminView):
    @doc(TAG, "Admin overview")
    def get(self, request: Request) -> Response:
        return Response(dashboards.admin_dashboard(user_of(request)))


# ---------------------------------------------------------------------------
# Products (8.1)
# ---------------------------------------------------------------------------


class AdminProductListView(AdminView):
    @doc(
        TAG,
        "Formulary products",
        parameters=query_params(["search", "is_active", "program_id", "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, {"search": "str", "is_active": "bool", "program_id": "uuid"})
        return page(request, self, catalog.list_products(query), each(catalog.product_payload))

    @doc(
        TAG,
        "Create a product",
        request=s.ProductInputSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.ProductInputSerializer, request)
        product = catalog.save_product(
            product=None, data=data, actor=user_of(request), request=request
        )
        return _created(catalog.product_payload(product))


class AdminProductDetailView(AdminView):
    @doc(TAG, "Product detail")
    def get(self, request: Request, product_id: str) -> Response:
        return Response(catalog.product_payload(catalog.get_product(product_id)))

    @doc(TAG, "Update a product", request=s.ProductInputSerializer)
    def patch(self, request: Request, product_id: str) -> Response:
        data = validated(s.ProductInputSerializer, request, partial=True)
        product = catalog.save_product(
            product=catalog.get_product(product_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.product_payload(product))


class AdminProductActiveView(AdminView):
    active = True

    @doc(TAG, "Activate or deactivate a product")
    def post(self, request: Request, product_id: str) -> Response:
        product = catalog.set_product_active(
            product=catalog.get_product(product_id),
            active=self.active,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.product_payload(product))


class AdminProductDeactivateView(AdminProductActiveView):
    active = False


# ---------------------------------------------------------------------------
# Programs, fees, packages (8.2, 8.3, 8.5)
# ---------------------------------------------------------------------------


class AdminProgramListView(AdminView):
    @doc(TAG, "Programs", parameters=query_params(["page", "page_size"]))
    def get(self, request: Request) -> Response:
        return page(request, self, catalog.programs(), each(catalog.program_payload))

    @doc(
        TAG,
        "Create a program",
        request=s.ProgramInputSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.ProgramInputSerializer, request)
        program = catalog.save_program(
            program=None, data=data, actor=user_of(request), request=request
        )
        return _created(catalog.program_payload(program))


class AdminProgramDetailView(AdminView):
    @doc(TAG, "Program detail")
    def get(self, request: Request, program_id: str) -> Response:
        return Response(catalog.program_payload(catalog.get_program(program_id)))

    @doc(TAG, "Update a program", request=s.ProgramInputSerializer)
    def patch(self, request: Request, program_id: str) -> Response:
        data = validated(s.ProgramInputSerializer, request, partial=True)
        program = catalog.save_program(
            program=catalog.get_program(program_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.program_payload(program))


class AdminProgramActiveView(AdminView):
    active = True

    @doc(TAG, "Activate or deactivate a program")
    def post(self, request: Request, program_id: str) -> Response:
        program = catalog.set_program_active(
            program=catalog.get_program(program_id),
            active=self.active,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.program_payload(program))


class AdminProgramDeactivateView(AdminProgramActiveView):
    active = False


class AdminProgramFeeView(AdminView):
    @doc(TAG, "Evaluation fee for a program")
    def get(self, request: Request, program_id: str) -> Response:
        return Response(catalog.fee_payload(catalog.get_program(program_id)))

    @doc(TAG, "Set the evaluation fee for a program", request=s.FeeSerializer)
    def put(self, request: Request, program_id: str) -> Response:
        data = validated(s.FeeSerializer, request)
        program = catalog.save_fee(
            program=catalog.get_program(program_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.fee_payload(program))


class AdminProgramPackagesView(AdminView):
    @doc(TAG, "Packages of a program", parameters=query_params(["page", "page_size"]))
    def get(self, request: Request, program_id: str) -> Response:
        program = catalog.get_program(program_id)
        queryset = program.packages.prefetch_related("items__product").order_by("position", "name")
        return page(request, self, queryset, each(catalog.package_payload))


class AdminProgramPackagesReorderView(AdminView):
    @doc(TAG, "Reorder the packages of a program", request=s.ReorderPackagesSerializer)
    def post(self, request: Request, program_id: str) -> Response:
        data = validated(s.ReorderPackagesSerializer, request)
        program = catalog.get_program(program_id)
        catalog.reorder_packages(
            program=program, ids=data["package_ids"], actor=user_of(request), request=request
        )
        queryset = program.packages.prefetch_related("items__product").order_by("position", "name")
        return Response([catalog.package_payload(package) for package in queryset])


class AdminPackageCreateView(AdminView):
    @doc(
        TAG,
        "Create a package",
        request=s.PackageInputSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.PackageInputSerializer, request)
        package = catalog.save_package(
            package=None, data=data, actor=user_of(request), request=request
        )
        return _created(catalog.package_payload(package))


class AdminPackageDetailView(AdminView):
    @doc(TAG, "Package detail")
    def get(self, request: Request, package_id: str) -> Response:
        return Response(catalog.package_payload(catalog.get_package(package_id)))

    @doc(TAG, "Update a package", request=s.PackageInputSerializer)
    def patch(self, request: Request, package_id: str) -> Response:
        data = validated(s.PackageInputSerializer, request, partial=True)
        if "program_id" in data:
            raise ValidationError({"program_id": ["A package cannot move to another program."]})
        package = catalog.save_package(
            package=catalog.get_package(package_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.package_payload(package))


class AdminPackageActiveView(AdminView):
    active = True

    @doc(TAG, "Activate or deactivate a package")
    def post(self, request: Request, package_id: str) -> Response:
        package = catalog.set_package_active(
            package=catalog.get_package(package_id),
            active=self.active,
            actor=user_of(request),
            request=request,
        )
        return Response(catalog.package_payload(package))


class AdminPackageDeactivateView(AdminPackageActiveView):
    active = False


# ---------------------------------------------------------------------------
# Questionnaires (8.4)
# ---------------------------------------------------------------------------


class AdminQuestionnaireListView(AdminView):
    @doc(
        TAG,
        "Questionnaires",
        parameters=query_params(["program_id", "status", "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        from apps.assessments.models import QuestionnaireStatus

        query = params(request, {"program_id": "uuid", "status": tuple(QuestionnaireStatus.values)})
        queryset = questionnaires.queryset()
        if query.get("program_id"):
            queryset = queryset.filter(program_id=query["program_id"])
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        queryset = queryset.order_by("program__name", "-version")
        return page(request, self, queryset, each(questionnaires.list_item))

    @doc(
        TAG,
        "Create a questionnaire draft",
        request=s.QuestionnaireCreateSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.QuestionnaireCreateSerializer, request)
        questionnaire = questionnaires.create(data=data, actor=user_of(request), request=request)
        return _created(questionnaires.detail(questionnaire))


class AdminQuestionnaireDetailView(AdminView):
    @doc(TAG, "Questionnaire content (admin view)")
    def get(self, request: Request, questionnaire_id: str) -> Response:
        return Response(questionnaires.detail(questionnaires.get(questionnaire_id)))

    @doc(TAG, "Update a draft's title and description", request=s.QuestionnaireUpdateSerializer)
    def patch(self, request: Request, questionnaire_id: str) -> Response:
        data = validated(s.QuestionnaireUpdateSerializer, request)
        questionnaire = questionnaires.update(
            questionnaire=questionnaires.get(questionnaire_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(questionnaires.detail(questionnaire))


class AdminQuestionnaireNewDraftView(AdminView):
    @doc(TAG, "Create a new draft from the published version", responses={201: OpenApiTypes.OBJECT})
    def post(self, request: Request, questionnaire_id: str) -> Response:
        questionnaire = questionnaires.new_draft(
            questionnaire=questionnaires.get(questionnaire_id),
            actor=user_of(request),
            request=request,
        )
        return _created(questionnaires.detail(questionnaire))


class AdminQuestionnaireStepsView(AdminView):
    @doc(TAG, "Add a step", request=s.StepInputSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request: Request, questionnaire_id: str) -> Response:
        data = validated(s.StepInputSerializer, request)
        questionnaire = questionnaires.get(questionnaire_id)
        questionnaires.add_step(
            questionnaire=questionnaire, data=data, actor=user_of(request), request=request
        )
        return _created(questionnaires.detail(questionnaires.get(questionnaire.pk)))


class AdminQuestionnaireStepDetailView(AdminView):
    @doc(TAG, "Update a step", request=s.StepInputSerializer)
    def patch(self, request: Request, step_id: str) -> Response:
        data = validated(s.StepInputSerializer, request, partial=True)
        step = questionnaires.update_step(
            step=questionnaires.get_step(step_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(questionnaires.detail(questionnaires.get(step.questionnaire_id)))

    @doc(TAG, "Delete a step", responses={204: None})
    def delete(self, request: Request, step_id: str) -> Response:
        questionnaires.delete_step(
            step=questionnaires.get_step(step_id), actor=user_of(request), request=request
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminStepQuestionsView(AdminView):
    @doc(
        TAG,
        "Add a question to a step",
        request=s.QuestionInputSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, step_id: str) -> Response:
        data = validated(s.QuestionInputSerializer, request)
        question = questionnaires.add_question(
            step=questionnaires.get_step(step_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return _created(questionnaires.question_payload(question))


class AdminQuestionDetailView(AdminView):
    @doc(TAG, "Update a question", request=s.QuestionInputSerializer)
    def patch(self, request: Request, question_id: str) -> Response:
        data = validated(s.QuestionInputSerializer, request, partial=True)
        question = questionnaires.update_question(
            question=questionnaires.get_question(question_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(questionnaires.question_payload(question))

    @doc(TAG, "Delete a question", responses={204: None})
    def delete(self, request: Request, question_id: str) -> Response:
        questionnaires.delete_question(
            question=questionnaires.get_question(question_id),
            actor=user_of(request),
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminQuestionnaireReorderView(AdminView):
    @doc(TAG, "Reorder steps and questions", request=s.QuestionnaireReorderSerializer)
    def post(self, request: Request, questionnaire_id: str) -> Response:
        data = validated(s.QuestionnaireReorderSerializer, request)
        questionnaire = questionnaires.reorder(
            questionnaire=questionnaires.get(questionnaire_id),
            steps=data["steps"],
            actor=user_of(request),
            request=request,
        )
        return Response(questionnaires.detail(questionnaire))


class AdminQuestionnaireValidateView(AdminView):
    @doc(TAG, "Validate a questionnaire", responses={200: s.ValidationResultSerializer})
    def post(self, request: Request, questionnaire_id: str) -> Response:
        errors = questionnaires.validate(questionnaires.get(questionnaire_id))
        return Response({"valid": not errors, "errors": errors})


class AdminQuestionnairePublishView(AdminView):
    @doc(TAG, "Publish a draft", request=s.PublishSerializer)
    def post(self, request: Request, questionnaire_id: str) -> Response:
        data = validated(s.PublishSerializer, request)
        questionnaire = questionnaires.publish(
            questionnaire=questionnaires.get(questionnaire_id),
            note=data.get("note", ""),
            actor=user_of(request),
            request=request,
        )
        return Response(questionnaires.detail(questionnaire))


class AdminQuestionnaireActiveView(AdminView):
    active = True

    @doc(TAG, "Activate or deactivate a questionnaire")
    def post(self, request: Request, questionnaire_id: str) -> Response:
        questionnaire = questionnaires.set_active(
            questionnaire=questionnaires.get(questionnaire_id),
            active=self.active,
            actor=user_of(request),
            request=request,
        )
        return Response(questionnaires.list_item(questionnaire))


class AdminQuestionnaireDeactivateView(AdminQuestionnaireActiveView):
    active = False


# ---------------------------------------------------------------------------
# Content and assets (9.1, 9.4, B1)
# ---------------------------------------------------------------------------


class AdminContentListView(AdminView):
    @doc(
        TAG,
        "Content items",
        parameters=query_params(["category", "status", "search", "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        from apps.catalog.models import ContentCategory, ContentStatus

        query = params(
            request,
            {
                "category": tuple(ContentCategory.values),
                "status": tuple(ContentStatus.values),
                "search": "str",
            },
        )
        return page(request, self, content.filtered(query), each(content.admin_payload))

    @doc(
        TAG,
        "Create a content item",
        request=s.ContentItemInputSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.ContentItemInputSerializer, request)
        item = content.save(item=None, data=data, actor=user_of(request), request=request)
        return _created(content.admin_payload(item))


class AdminContentDetailView(AdminView):
    @doc(TAG, "Content item")
    def get(self, request: Request, item_id: str) -> Response:
        return Response(content.admin_payload(content.get(item_id)))

    @doc(TAG, "Update a content item", request=s.ContentItemInputSerializer)
    def patch(self, request: Request, item_id: str) -> Response:
        data = validated(s.ContentItemInputSerializer, request, partial=True)
        item = content.save(
            item=content.get(item_id), data=data, actor=user_of(request), request=request
        )
        return Response(content.admin_payload(item))

    @doc(TAG, "Delete a draft content item", responses={204: None})
    def delete(self, request: Request, item_id: str) -> Response:
        content.delete(item=content.get(item_id), actor=user_of(request), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminContentPublishView(AdminView):
    publish = True

    @doc(TAG, "Publish or unpublish a content item")
    def post(self, request: Request, item_id: str) -> Response:
        item = content.set_status(
            item=content.get(item_id), publish=self.publish, actor=user_of(request), request=request
        )
        return Response(content.admin_payload(content.get(item.pk)))


class AdminContentUnpublishView(AdminContentPublishView):
    publish = False


class AdminContentPreviewView(AdminView):
    @doc(TAG, "Preview a content item as the public card")
    def get(self, request: Request, item_id: str) -> Response:
        return Response(content.public_payload(content.get(item_id)))


class PublicContentItemsView(APIView):
    schema = PortalSchema()
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    @doc("public", "Landing-page content items", auth=[], responses={200: OpenApiTypes.OBJECT})
    def get(self, request: Request) -> Response:
        response = Response(content.public_items())
        response["Cache-Control"] = "public, max-age=300"
        return response


class AdminAssetUploadView(AdminView):
    @doc(
        TAG,
        "Start a pre-signed image upload",
        request=s.AssetUploadCreateSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.AssetUploadCreateSerializer, request)
        asset, target = content.start_upload(data=data, actor=user_of(request))
        return _created({"id": asset.pk, "upload": target})


class AdminAssetConfirmView(AdminView):
    @doc(TAG, "Validate and re-encode an uploaded image", responses={200: s.AssetReadySerializer})
    def post(self, request: Request, asset_id: str) -> Response:
        asset = content.confirm_upload(asset_id=asset_id, actor=user_of(request), request=request)
        return Response(
            {"id": asset.pk, "status": asset.status, "url": content.public_asset_url(asset)}
        )


# ---------------------------------------------------------------------------
# Reference data (9.2) and legal documents (9.3)
# ---------------------------------------------------------------------------


class AdminReferenceListView(AdminView):
    resource = ""

    @doc(
        TAG,
        "Reference data list",
        parameters=query_params(["search", "is_active", "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, {"search": "str", "is_active": ("true", "false")})
        queryset = reference.filtered(self.resource, query)
        return page(
            request, self, queryset, each(lambda obj: reference.payload(self.resource, obj))
        )

    @doc(
        TAG,
        "Create reference data",
        request=OpenApiTypes.OBJECT,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.REFERENCE_SERIALIZERS[self.resource], request)
        obj = reference.save(
            self.resource, obj=None, data=data, actor=user_of(request), request=request
        )
        return _created(reference.payload(self.resource, obj))


class AdminReferenceDetailView(AdminView):
    resource = ""

    @doc(TAG, "Reference data item")
    def get(self, request: Request, pk: str) -> Response:
        return Response(reference.payload(self.resource, reference.get(self.resource, pk)))

    @doc(TAG, "Update reference data", request=OpenApiTypes.OBJECT)
    def patch(self, request: Request, pk: str) -> Response:
        data = validated(s.REFERENCE_SERIALIZERS[self.resource], request, partial=True)
        obj = reference.save(
            self.resource,
            obj=reference.get(self.resource, pk),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(reference.payload(self.resource, obj))

    @doc(TAG, "Delete (or deactivate when in use)", responses={204: None})
    def delete(self, request: Request, pk: str) -> Response:
        reference.delete(
            self.resource,
            obj=reference.get(self.resource, pk),
            actor=user_of(request),
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


def reference_views(resource: str) -> tuple[Any, Any]:
    name = "".join(part.title() for part in resource.split("-"))
    list_view = type(f"Admin{name}ListView", (AdminReferenceListView,), {"resource": resource})
    detail_view = type(
        f"Admin{name}DetailView", (AdminReferenceDetailView,), {"resource": resource}
    )
    return list_view, detail_view


class AdminLegalListView(AdminView):
    @doc(
        TAG,
        "Legal document versions",
        parameters=query_params(["document_type", "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        from apps.accounts.models import ConsentRecord

        query = params(request, {"document_type": tuple(ConsentRecord.DocumentType.values)})
        return page(request, self, reference.legal_documents(query), each(reference.legal_payload))

    @doc(
        TAG,
        "Create a legal document draft",
        request=s.LegalDocumentInputSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.LegalDocumentInputSerializer, request)
        document = reference.create_legal(data=data, actor=user_of(request), request=request)
        return _created(reference.legal_payload(document))


class AdminLegalDetailView(AdminView):
    @doc(TAG, "Update a legal document draft", request=s.LegalDocumentInputSerializer)
    def patch(self, request: Request, document_id: str) -> Response:
        data = validated(s.LegalDocumentInputSerializer, request, partial=True)
        document = reference.update_legal(
            document=reference.get_legal(document_id),
            data=data,
            actor=user_of(request),
            request=request,
        )
        return Response(reference.legal_payload(document))


class AdminLegalPublishView(AdminView):
    @doc(TAG, "Publish a legal document draft")
    def post(self, request: Request, document_id: str) -> Response:
        document = reference.publish_legal(
            document=reference.get_legal(document_id), actor=user_of(request), request=request
        )
        return Response(reference.legal_payload(document))


# ---------------------------------------------------------------------------
# Settings (9.5)
# ---------------------------------------------------------------------------


class AdminSettingsView(AdminView):
    @doc(TAG, "Read a settings group")
    def get(self, request: Request, group: str) -> Response:
        return Response(platform_settings.read(group))

    @doc(
        TAG,
        "Replace a settings group (body: the group's fields plus reason)",
        request=OpenApiTypes.OBJECT,
    )
    def put(self, request: Request, group: str) -> Response:
        platform_settings.read(group)  # 404 for unknown groups
        body = dict(request.data) if isinstance(request.data, dict) else {}
        reason = str(body.pop("reason", "") or "").strip()
        if not 5 <= len(reason) <= 300:
            raise ValidationError({"reason": ["Between 5 and 300 characters."]})
        serializer = s.SETTINGS_SERIALIZERS[group](data=body)
        serializer.is_valid(raise_exception=True)
        data = platform_settings.write(
            group=group,
            data=dict(serializer.validated_data),
            reason=reason,
            actor=user_of(request),
            request=request,
        )
        return Response(data)


# ---------------------------------------------------------------------------
# Reports (10) and audit logs (11.1)
# ---------------------------------------------------------------------------

REPORT_PARAMS = ["from", "to", "timezone"]


class AdminReportOverviewView(AdminView):
    @doc(TAG, "Reports overview", parameters=query_params(REPORT_PARAMS))
    def get(self, request: Request) -> Response:
        return Response(reports.overview(reports.parse_period(request.query_params)))


class AdminReportRevenueView(AdminView):
    @doc(TAG, "Revenue series", parameters=query_params([*REPORT_PARAMS, "granularity", "compare"]))
    def get(self, request: Request) -> Response:
        period = reports.parse_period(request.query_params)
        return Response(
            reports.revenue(
                period,
                granularity=request.query_params.get("granularity") or "MONTH",
                compare=request.query_params.get("compare") or "NONE",
            )
        )


class AdminReportExportView(AdminView):
    @doc(
        TAG,
        "Request a CSV export",
        request=s.ExportCreateSerializer,
        responses={202: s.ExportCreatedSerializer},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.ExportCreateSerializer, request)
        export = reports.request_export(data=data, actor=user_of(request), request=request)
        return Response({"id": export.pk, "status": export.status}, status=status.HTTP_202_ACCEPTED)


class AdminReportExportDetailView(AdminView):
    @doc(TAG, "Export status and download link", responses={200: s.ExportStatusSerializer})
    def get(self, request: Request, export_id: str) -> Response:
        body = reports.export_payload(reports.get_export(export_id))
        body.pop("id")
        return Response(body)


AUDIT_FILTERS = {
    "actor_id": "uuid",
    "action": "str",
    "object_type": "str",
    "object_id": "str",
    "patient_id": "uuid",
    "from": "str",
    "to": "str",
}


class AdminAuditLogView(AdminView):
    @doc(
        TAG,
        "Audit log (append-only)",
        parameters=query_params([*AUDIT_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, AUDIT_FILTERS)
        return page(request, self, dashboards.audit_logs(query), each(dashboards.audit_payload))


# ---------------------------------------------------------------------------
# Privacy requests (11.2)
# ---------------------------------------------------------------------------


class PatientPrivacyRequestView(APIView):
    schema = PortalSchema()
    permission_classes = [IsPatient]
    throttle_scope = "privacy_request"

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return []

    @doc("privacy", "My privacy requests", responses={200: s.PrivacyRequestSerializer(many=True)})
    def get(self, request: Request) -> Response:
        rows = privacy.for_patient(user_of(request))
        return Response([privacy.patient_payload(row) for row in rows])

    @doc(
        "privacy",
        "File a privacy request",
        request=s.PrivacyRequestCreateSerializer,
        responses={201: s.PrivacyRequestSerializer},
    )
    def post(self, request: Request) -> Response:
        data = validated(s.PrivacyRequestCreateSerializer, request)
        row = privacy.create(user=user_of(request), data=data, request=request)
        return _created(privacy.patient_payload(row))


class AdminPrivacyListView(AdminView):
    @doc(TAG, "Privacy requests", parameters=query_params(["status", "type", "page", "page_size"]))
    def get(self, request: Request) -> Response:
        from apps.backoffice.models import PrivacyRequestStatus, PrivacyRequestType

        query = params(
            request,
            {
                "status": tuple(PrivacyRequestStatus.values),
                "type": tuple(PrivacyRequestType.values),
            },
        )
        return page(request, self, privacy.filtered(query), each(privacy.admin_payload))


class AdminPrivacyDetailView(AdminView):
    @doc(TAG, "Privacy request detail (audited)")
    def get(self, request: Request, request_id: str) -> Response:
        row = privacy.get(request_id, actor=user_of(request), request=request)
        return Response(privacy.admin_payload(row))


class AdminPrivacyStatusView(AdminView):
    @doc(TAG, "Change privacy request status", request=s.PrivacyStatusSerializer)
    def post(self, request: Request, request_id: str) -> Response:
        data = validated(s.PrivacyStatusSerializer, request)
        actor = user_of(request)
        row = privacy.set_status(
            privacy_request=privacy.get(request_id, actor=actor, request=request),
            status=data["status"],
            note=data.get("note", ""),
            actor=actor,
            request=request,
        )
        return Response(privacy.admin_payload(row))


class AdminPrivacyFulfillView(AdminView):
    @doc(TAG, "Fulfil an access-export or deletion request")
    def post(self, request: Request, request_id: str) -> Response:
        actor = user_of(request)
        row = privacy.fulfill(
            privacy_request=privacy.get(request_id, actor=actor, request=request),
            actor=actor,
            request=request,
        )
        return Response(privacy.admin_payload(row))
