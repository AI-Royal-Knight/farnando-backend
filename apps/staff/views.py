"""Staff portal endpoints (Part 5, sections 2.2 and 3 to 7)."""

from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.serializers import UserSummarySerializer
from apps.accounts.services import issue_tokens
from apps.backoffice import dashboards
from apps.backoffice.serializers import (
    AssignPhysicianSerializer,
    ComplaintRespondSerializer,
    ComplaintStatusSerializer,
    EvaluationAssignSerializer,
    EvaluationPrioritySerializer,
    OrderNoteSerializer,
    OrderTransitionSerializer,
    RefundResponseSerializer,
    RefundSerializer,
    ReopenSerializer,
    StaffInvitationAcceptSerializer,
    StaffInvitationInfoSerializer,
    StaffOptionalReasonSerializer,
    StaffReasonSerializer,
    StaffRequestLabsSerializer,
    TicketAssignSerializer,
    TicketPrioritySerializer,
    TicketReplySerializer,
    TicketStatusSerializer,
    TrackingSerializer,
)
from apps.clinical.evaluations import parse_if_match
from apps.orders.models import OrderStatus, SubscriptionStatus
from apps.orders.serializers import OrderSerializer
from apps.staff import accounts, commerce, patients
from apps.staff import evaluations as staff_evaluations
from apps.staff.api import (
    CONCURRENCY_ERRORS,
    IDEMPOTENCY,
    IF_MATCH,
    PortalSchema,
    doc,
    each,
    idempotent,
    page,
    params,
    query_params,
    user_of,
    validated,
)
from apps.staff.models import Permission
from apps.staff.permissions import IsStaffOrAdmin, StaffPermission, require_perm
from apps.support import complaints, tickets
from apps.support.models import ComplaintBookEntry, TicketPriority, TicketSource, TicketStatus

TAG = "staff"


class StaffView(APIView):
    schema = PortalSchema()
    permission_classes = [StaffPermission]


# ---------------------------------------------------------------------------
# Staff invitations (2.2)
# ---------------------------------------------------------------------------


class StaffInvitationDetailView(APIView):
    schema = PortalSchema()
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "staff_invitation_lookup"

    @doc(
        "public",
        "Validate a staff invitation link",
        auth=[],
        responses={200: StaffInvitationInfoSerializer},
    )
    def get(self, request: Request, token: str) -> Response:
        invitation = accounts.resolve_staff_invitation(token)
        body = {
            "email": invitation.email,
            "full_name": invitation.full_name,
            "staff_type": invitation.staff_type,
            "expires_at": invitation.expires_at,
        }
        return Response(StaffInvitationInfoSerializer(body).data)


class StaffInvitationAcceptView(APIView):
    schema = PortalSchema()
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "staff_invitation_accept"

    @doc(
        "public",
        "Set a password and activate a staff account",
        auth=[],
        request=StaffInvitationAcceptSerializer,
    )
    def post(self, request: Request, token: str) -> Response:
        accounts.resolve_staff_invitation(token)
        data = validated(StaffInvitationAcceptSerializer, request)
        user = accounts.accept_staff_invitation(token=token, data=data, request=request)
        body = {
            "user": UserSummarySerializer(user).data,
            "tokens": issue_tokens(user),
            "mfa_enrollment_required": True,
        }
        return Response(body, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Dashboard (3.1)
# ---------------------------------------------------------------------------


class StaffDashboardView(APIView):
    schema = PortalSchema()
    permission_classes = [IsStaffOrAdmin]

    @doc(TAG, "Staff overview")
    def get(self, request: Request) -> Response:
        return Response(dashboards.staff_dashboard(user_of(request)))


# ---------------------------------------------------------------------------
# Patients (4)
# ---------------------------------------------------------------------------

PATIENT_FILTERS = {
    "search": "str",
    "status": ("PENDING", "ACTIVE", "NOT_SUITABLE", "INACTIVE", "SUSPENDED"),
    "physician_id": "uuid",
    "program_id": "uuid",
    "ordering": ("-last_activity_at", "name", "-created_at"),
}


class StaffPatientListView(StaffView):
    required_permission = Permission.PATIENTS_READ

    @doc(
        TAG,
        "Patient management list",
        parameters=query_params([*PATIENT_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, PATIENT_FILTERS)
        viewer = user_of(request)
        return page(
            request,
            self,
            patients.filtered(query),
            each(lambda patient: patients.row_payload(patient, viewer=viewer)),
            summary=patients.summary(query),
        )


class StaffPatientDetailView(StaffView):
    required_permission = Permission.PATIENTS_READ

    @doc(TAG, "Patient detail (audited)")
    def get(self, request: Request, patient_id: str) -> Response:
        patient = patients.get_patient(patient_id)
        return Response(patients.detail_payload(patient, viewer=user_of(request), request=request))


class StaffPatientAssignView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(TAG, "Assign or re-assign the care physician", request=AssignPhysicianSerializer)
    def post(self, request: Request, patient_id: str) -> Response:
        data = validated(AssignPhysicianSerializer, request)
        viewer = user_of(request)
        patient = patients.assign_physician(
            patient=patients.get_patient(patient_id),
            physician_id=data["physician_id"],
            reason=data.get("reason", ""),
            viewer=viewer,
            request=request,
        )
        return Response(patients.row_payload(patients.get_patient(patient.pk), viewer=viewer))


class StaffPatientSuspendView(StaffView):
    required_permission = Permission.PATIENTS_MANAGE

    @doc(TAG, "Suspend a patient account", request=StaffReasonSerializer)
    def post(self, request: Request, patient_id: str) -> Response:
        data = validated(StaffReasonSerializer, request)
        viewer = user_of(request)
        patient = patients.suspend(
            patient=patients.get_patient(patient_id),
            reason=data["reason"],
            viewer=viewer,
            request=request,
        )
        return Response(patients.row_payload(patients.get_patient(patient.pk), viewer=viewer))


class StaffPatientReactivateView(StaffView):
    required_permission = Permission.PATIENTS_MANAGE

    @doc(TAG, "Reactivate a suspended patient")
    def post(self, request: Request, patient_id: str) -> Response:
        viewer = user_of(request)
        patient = patients.reactivate(
            patient=patients.get_patient(patient_id), viewer=viewer, request=request
        )
        return Response(patients.row_payload(patients.get_patient(patient.pk), viewer=viewer))


# ---------------------------------------------------------------------------
# Evaluations (5)
# ---------------------------------------------------------------------------

EVALUATION_FILTERS = {
    "status_group": tuple(staff_evaluations.STATUS_GROUPS),
    "assignment": ("UNASSIGNED", "ASSIGNED"),
    "priority": ("NORMAL", "URGENT"),
    "labs": ("ATTACHED", "SCANNING", "MISSING"),
    "program_id": "uuid",
    "physician_id": "uuid",
    "search": "str",
    "from": "date",
    "to": "date",
    "ordering": ("submitted_at", "-submitted_at", "bmi"),
    "review_requested": "bool",
}


def _evaluation_item(evaluation_id: Any) -> dict[str, Any]:
    evaluation = staff_evaluations.get(evaluation_id)
    return staff_evaluations.list_payloads([evaluation])[0]


class StaffEvaluationListView(StaffView):
    required_permission = Permission.EVALUATIONS_READ

    @doc(
        TAG,
        "Evaluations with status-group counts",
        parameters=query_params([*EVALUATION_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, EVALUATION_FILTERS)
        return page(
            request,
            self,
            staff_evaluations.filtered(query),
            staff_evaluations.list_payloads,
            counts=staff_evaluations.counts(query),
        )


class StaffEvaluationDetailView(StaffView):
    required_permission = Permission.EVALUATIONS_READ

    @doc(TAG, "Evaluation detail (clinical blocks need CLINICAL_READ)")
    def get(self, request: Request, evaluation_id: str) -> Response:
        evaluation = staff_evaluations.get(evaluation_id)
        body = staff_evaluations.detail_payload(evaluation, user=user_of(request), request=request)
        response = Response(body)
        response["ETag"] = f'"{evaluation.version}"'
        return response


class StaffPhysicianListView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(TAG, "Physicians available for assignment")
    def get(self, request: Request) -> Response:
        return Response(staff_evaluations.assignable_physicians())


class StaffEvaluationRecommendedView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(TAG, "Recommended physicians for an evaluation")
    def get(self, request: Request, evaluation_id: str) -> Response:
        evaluation = staff_evaluations.get(evaluation_id)
        return Response(staff_evaluations.recommendations(evaluation, limit=5))


class StaffEvaluationAssignView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(
        TAG,
        "Assign (reserve) an evaluation to a physician",
        request=EvaluationAssignSerializer,
        parameters=[IF_MATCH, IDEMPOTENCY],
        responses={200: OpenApiTypes.OBJECT, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        version = parse_if_match(request.headers.get("If-Match"))
        data = validated(EvaluationAssignSerializer, request)
        viewer = user_of(request)

        def run() -> dict[str, Any]:
            evaluation = staff_evaluations.assign(
                evaluation_id=evaluation_id,
                physician_id=data["physician_id"],
                note=data.get("note", ""),
                version=version,
                user=viewer,
                request=request,
            )
            return _evaluation_item(evaluation.pk)

        return idempotent(request, f"staff-evaluation-assign:{evaluation_id}", run)


class StaffEvaluationUnassignView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(
        TAG,
        "Return a reserved evaluation to the open queue",
        request=StaffOptionalReasonSerializer,
        parameters=[IF_MATCH, IDEMPOTENCY],
        responses={200: OpenApiTypes.OBJECT, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        version = parse_if_match(request.headers.get("If-Match"))
        data = validated(StaffOptionalReasonSerializer, request)
        viewer = user_of(request)

        def run() -> dict[str, Any]:
            evaluation = staff_evaluations.unassign(
                evaluation_id=evaluation_id,
                reason=data.get("reason", ""),
                version=version,
                user=viewer,
                request=request,
            )
            return _evaluation_item(evaluation.pk)

        return idempotent(request, f"staff-evaluation-unassign:{evaluation_id}", run)


class StaffEvaluationPriorityView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(TAG, "Set evaluation priority", request=EvaluationPrioritySerializer)
    def post(self, request: Request, evaluation_id: str) -> Response:
        data = validated(EvaluationPrioritySerializer, request)
        evaluation = staff_evaluations.set_priority(
            evaluation_id=evaluation_id,
            priority=data["priority"],
            user=user_of(request),
            request=request,
        )
        return Response(_evaluation_item(evaluation.pk))


class StaffEvaluationRequestLabsView(StaffView):
    required_permission = Permission.EVALUATIONS_REQUEST_LABS

    @doc(
        TAG,
        "Request lab tests (SUBMITTED -> PENDING_LABS)",
        request=StaffRequestLabsSerializer,
        parameters=[IF_MATCH, IDEMPOTENCY],
        responses={200: OpenApiTypes.OBJECT, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        version = parse_if_match(request.headers.get("If-Match"))
        data = validated(StaffRequestLabsSerializer, request)
        viewer = user_of(request)

        def run() -> dict[str, Any]:
            evaluation = staff_evaluations.request_labs(
                evaluation_id=evaluation_id,
                version=version,
                data=data,
                user=viewer,
                request=request,
            )
            return _evaluation_item(evaluation.pk)

        return idempotent(request, f"staff-evaluation-labs:{evaluation_id}", run)


class StaffEvaluationReopenView(StaffView):
    required_permission = Permission.EVALUATIONS_ASSIGN

    @doc(
        TAG,
        "Reopen a screened-out evaluation after a review request",
        request=ReopenSerializer,
        parameters=[IF_MATCH, IDEMPOTENCY],
        responses={200: OpenApiTypes.OBJECT, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        version = parse_if_match(request.headers.get("If-Match"))
        data = validated(ReopenSerializer, request)
        viewer = user_of(request)

        def run() -> dict[str, Any]:
            evaluation = staff_evaluations.reopen(
                evaluation_id=evaluation_id,
                version=version,
                note=data["note"],
                user=viewer,
                request=request,
            )
            return _evaluation_item(evaluation.pk)

        return idempotent(request, f"staff-evaluation-reopen:{evaluation_id}", run)


# ---------------------------------------------------------------------------
# Tickets (6.3)
# ---------------------------------------------------------------------------

TICKET_FILTERS = {
    "status": tuple(TicketStatus.values),
    "priority": tuple(TicketPriority.values),
    "category_code": "str",
    "assigned_to": "str",
    "source": tuple(TicketSource.values),
    "search": "str",
    "from": "date",
    "to": "date",
    "ordering": ("-created_at", "priority"),
}


class StaffTicketListView(StaffView):
    required_permission = Permission.TICKETS_MANAGE

    @doc(
        TAG,
        "Support tickets with KPI summary",
        parameters=query_params([*TICKET_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, TICKET_FILTERS)
        assigned = query.get("assigned_to")
        if assigned and assigned not in ("me", "unassigned"):
            params(request, {"assigned_to": "uuid"})
        viewer = user_of(request)
        queryset = tickets.staff_filtered(query, viewer=viewer)
        return page(
            request,
            self,
            queryset,
            each(lambda ticket: tickets.ticket_payload(ticket, viewer=viewer)),
            summary=tickets.staff_summary(tickets.staff_filtered({}, viewer=viewer)),
        )


class StaffTicketDetailView(StaffView):
    required_permission = Permission.TICKETS_MANAGE

    @doc(TAG, "Ticket detail with messages and history", parameters=query_params(["view"]))
    def get(self, request: Request, ticket_id: str) -> Response:
        viewer = user_of(request)
        view = params(request, {"view": ("DEFAULT", "CHAT")}).get("view", "DEFAULT")
        ticket = tickets.staff_ticket(ticket_id, viewer)
        return Response(tickets.staff_detail(ticket, viewer=viewer, view=view, request=request))


class StaffTicketAssignView(StaffView):
    required_permission = Permission.TICKETS_MANAGE

    @doc(TAG, "Assign a ticket", request=TicketAssignSerializer)
    def post(self, request: Request, ticket_id: str) -> Response:
        data = validated(TicketAssignSerializer, request)
        viewer = user_of(request)
        ticket = tickets.assign(
            ticket_id=ticket_id, staff_id=data["staff_id"], viewer=viewer, request=request
        )
        return Response(tickets.ticket_payload(ticket, viewer=viewer))


class StaffTicketReplyView(StaffView):
    required_permission = Permission.TICKETS_MANAGE

    @doc(
        TAG,
        "Reply or add an internal note",
        request=TicketReplySerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, ticket_id: str) -> Response:
        data = validated(TicketReplySerializer, request)
        message = tickets.reply(
            ticket_id=ticket_id,
            body=data["body"],
            is_internal=data["is_internal"],
            viewer=user_of(request),
            request=request,
        )
        return Response(tickets.message_payload(message), status=status.HTTP_201_CREATED)


class StaffTicketStatusView(StaffView):
    required_permission = Permission.TICKETS_MANAGE

    @doc(TAG, "Change ticket status", request=TicketStatusSerializer)
    def post(self, request: Request, ticket_id: str) -> Response:
        data = validated(TicketStatusSerializer, request)
        viewer = user_of(request)
        ticket = tickets.set_status(
            ticket_id=ticket_id, status=data["status"], viewer=viewer, request=request
        )
        return Response(tickets.ticket_payload(ticket, viewer=viewer))


class StaffTicketPriorityView(StaffView):
    required_permission = Permission.TICKETS_MANAGE

    @doc(TAG, "Change ticket priority", request=TicketPrioritySerializer)
    def post(self, request: Request, ticket_id: str) -> Response:
        data = validated(TicketPrioritySerializer, request)
        viewer = user_of(request)
        ticket = tickets.set_priority(
            ticket_id=ticket_id, priority=data["priority"], viewer=viewer, request=request
        )
        return Response(tickets.ticket_payload(ticket, viewer=viewer))


# ---------------------------------------------------------------------------
# Complaints Book (6.4)
# ---------------------------------------------------------------------------

COMPLAINT_FILTERS = {
    "status": tuple(ComplaintBookEntry.Status.values),
    "filing_type": tuple(ComplaintBookEntry.FilingType.values),
    "overdue": "bool",
    "category_code": "str",
    "search": "str",
    "from": "date",
    "to": "date",
}


class StaffComplaintListView(StaffView):
    required_permission = Permission.COMPLAINTS_MANAGE

    @doc(
        TAG,
        "Complaints Book queue",
        parameters=query_params([*COMPLAINT_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, COMPLAINT_FILTERS)
        return page(
            request,
            self,
            complaints.filtered(query),
            each(complaints.item_payload),
            summary=complaints.summary(),
        )


class StaffComplaintDetailView(StaffView):
    required_permission = Permission.COMPLAINTS_MANAGE

    @doc(TAG, "Complaint record with responses and history")
    def get(self, request: Request, complaint_number: str) -> Response:
        entry = complaints.get(complaint_number)
        return Response(complaints.detail_payload(entry, viewer=user_of(request), request=request))


class StaffComplaintRespondView(StaffView):
    required_permission = Permission.COMPLAINTS_MANAGE

    @doc(
        TAG,
        "Respond to a complaint",
        request=ComplaintRespondSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, complaint_number: str) -> Response:
        data = validated(ComplaintRespondSerializer, request)
        viewer = user_of(request)
        complaints.respond(
            complaint_number=complaint_number,
            body=data["body"],
            resolution=data["resolution"],
            viewer=viewer,
            request=request,
        )
        entry = complaints.get(complaint_number)
        return Response(
            complaints.detail_payload(entry, viewer=viewer, request=request),
            status=status.HTTP_201_CREATED,
        )


class StaffComplaintStatusView(StaffView):
    required_permission = Permission.COMPLAINTS_MANAGE

    @doc(TAG, "Change complaint status", request=ComplaintStatusSerializer)
    def post(self, request: Request, complaint_number: str) -> Response:
        data = validated(ComplaintStatusSerializer, request)
        viewer = user_of(request)
        entry = complaints.set_status(
            complaint_number=complaint_number,
            status=data["status"],
            note=data.get("note", ""),
            viewer=viewer,
            request=request,
        )
        return Response(complaints.item_payload(entry))


# ---------------------------------------------------------------------------
# Orders (7.1)
# ---------------------------------------------------------------------------

ORDER_FILTERS = {
    "status_group": tuple(commerce.STATUS_GROUPS),
    "status": tuple(OrderStatus.values),
    "search": "str",
    "from": "date",
    "to": "date",
    "program_id": "uuid",
    "ordering": ("-placed_at", "placed_at"),
}


class StaffOrderListView(StaffView):
    required_permission = Permission.ORDERS_READ

    @doc(
        TAG,
        "Orders with status-group summary",
        parameters=query_params([*ORDER_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, ORDER_FILTERS)
        return page(
            request,
            self,
            commerce.filtered_orders(query),
            each(commerce.order_row),
            summary=commerce.order_summary(),
        )


class StaffOrderDetailView(StaffView):
    required_permission = Permission.ORDERS_READ

    @doc(TAG, "Order detail")
    def get(self, request: Request, order_id: str) -> Response:
        order = commerce.get_order(order_id)
        return Response(commerce.order_detail(order, viewer=user_of(request), request=request))


class StaffOrderTransitionView(StaffView):
    required_permission = Permission.ORDERS_MANAGE

    @doc(
        TAG,
        "Move an order through fulfilment",
        request=OrderTransitionSerializer,
        parameters=[IDEMPOTENCY],
    )
    def post(self, request: Request, order_id: str) -> Response:
        data = validated(OrderTransitionSerializer, request)
        viewer = user_of(request)

        def run() -> dict[str, Any]:
            order = commerce.transition(
                order_id=order_id, data=data, viewer=viewer, request=request
            )
            return commerce.order_detail(order, viewer=viewer, request=request)

        return idempotent(request, f"staff-order-transition:{order_id}", run)


class StaffOrderTrackingView(StaffView):
    required_permission = Permission.ORDERS_MANAGE

    @doc(TAG, "Correct tracking data", request=TrackingSerializer)
    def patch(self, request: Request, order_id: str) -> Response:
        data = validated(TrackingSerializer, request)
        viewer = user_of(request)
        order = commerce.update_tracking(
            order_id=order_id, data=data, viewer=viewer, request=request
        )
        return Response(commerce.order_detail(order, viewer=viewer, request=request))


class StaffOrderNoteView(StaffView):
    required_permission = Permission.ORDERS_MANAGE

    @doc(
        TAG,
        "Add an internal order note",
        request=OrderNoteSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, order_id: str) -> Response:
        data = validated(OrderNoteSerializer, request)
        note = commerce.add_note(
            order_id=order_id, body=data["body"], viewer=user_of(request), request=request
        )
        author = note.author
        body = {
            "id": note.pk,
            "body": note.body,
            "author": {"id": author.pk, "name": author.get_full_name()} if author else None,
            "created_at": note.created_at,
        }
        return Response(body, status=status.HTTP_201_CREATED)


class StaffOrderCancelView(StaffView):
    required_permission = Permission.ORDERS_MANAGE

    @doc(TAG, "Cancel an order before shipping", request=StaffReasonSerializer)
    def post(self, request: Request, order_id: str) -> Response:
        data = validated(StaffReasonSerializer, request)
        viewer = user_of(request)
        order = commerce.cancel(
            order_id=order_id, reason=data["reason"], viewer=viewer, request=request
        )
        return Response(commerce.order_detail(order, viewer=viewer, request=request))


# ---------------------------------------------------------------------------
# Subscriptions (7.2)
# ---------------------------------------------------------------------------

SUBSCRIPTION_FILTERS = {
    "status": tuple(SubscriptionStatus.values),
    "scope": ("ALL", "REFILL_DUE"),
    "search": "str",
    "program_id": "uuid",
}


class StaffSubscriptionListView(StaffView):
    required_permission = Permission.ORDERS_READ

    @doc(
        TAG,
        "Subscriptions with status summary",
        parameters=query_params([*SUBSCRIPTION_FILTERS, "page", "page_size"]),
    )
    def get(self, request: Request) -> Response:
        query = params(request, SUBSCRIPTION_FILTERS)
        return page(
            request,
            self,
            commerce.filtered_subscriptions(query),
            each(commerce.subscription_row),
            summary=commerce.subscription_summary(),
        )


class StaffSubscriptionDetailView(StaffView):
    required_permission = Permission.ORDERS_READ

    @doc(TAG, "Subscription detail")
    def get(self, request: Request, subscription_id: str) -> Response:
        subscription = commerce.get_subscription(subscription_id)
        return Response(
            commerce.subscription_detail(subscription, viewer=user_of(request), request=request)
        )


class StaffSubscriptionActionView(StaffView):
    required_permission = Permission.SUBSCRIPTIONS_MANAGE
    action = ""

    @doc(TAG, "Pause, resume or cancel a subscription", request=StaffReasonSerializer)
    def post(self, request: Request, subscription_id: str) -> Response:
        data = validated(StaffReasonSerializer, request)
        viewer = user_of(request)
        subscription = commerce.subscription_action(
            subscription_id=subscription_id,
            action=self.action,
            reason=data["reason"],
            viewer=viewer,
            request=request,
        )
        return Response(commerce.subscription_detail(subscription, viewer=viewer, request=request))


class StaffSubscriptionPauseView(StaffSubscriptionActionView):
    action = "pause"


class StaffSubscriptionResumeView(StaffSubscriptionActionView):
    action = "resume"


class StaffSubscriptionCancelView(StaffSubscriptionActionView):
    action = "cancel"


class StaffSubscriptionRefillView(StaffView):
    required_permission = Permission.SUBSCRIPTIONS_MANAGE

    @doc(
        TAG,
        "Create an out-of-cycle refill order",
        parameters=[IDEMPOTENCY],
        responses={201: OrderSerializer},
    )
    def post(self, request: Request, subscription_id: str) -> Response:
        viewer = user_of(request)
        key = request.headers.get("Idempotency-Key") or ""

        def run() -> dict[str, Any]:
            order = commerce.new_refill(
                subscription_id=subscription_id,
                viewer=viewer,
                idempotency_key=key,
                request=request,
            )
            order = commerce.get_order(order.pk)
            return dict(OrderSerializer(order).data)

        return idempotent(
            request,
            f"staff-subscription-refill:{subscription_id}",
            run,
            status_code=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Refunds (7.3)
# ---------------------------------------------------------------------------


class StaffRefundView(StaffView):
    required_permission = Permission.ORDERS_REFUND

    @doc(
        TAG,
        "Refund a payment through the provider",
        request=RefundSerializer,
        parameters=[IDEMPOTENCY],
        responses={202: RefundResponseSerializer},
    )
    def post(self, request: Request, payment_id: str) -> Response:
        data = validated(RefundSerializer, request)
        viewer = user_of(request)
        require_perm(viewer, Permission.ORDERS_REFUND)

        def run() -> dict[str, Any]:
            refund = commerce.refund(
                payment_id=payment_id, data=data, viewer=viewer, request=request
            )
            return {
                "payment_id": refund.payment_id,
                "status": "PENDING",
                "amount": {"amount": f"{refund.amount:.2f}", "currency": refund.currency},
            }

        return idempotent(
            request, f"staff-refund:{payment_id}", run, status_code=status.HTTP_202_ACCEPTED
        )
