from .models import Invoice
from .models import Expense
from django.utils import timezone
from django.db.models import Sum

def debt_invoices_sidebar(request):
    # Only show unpaid debts
    return {
        'debt_invoices_sidebar': Invoice.objects.filter(status='debt', paid=False)
    }


def today_expenses(request):
    """
    Add 'today_expense_total' and 'today_expenses_list' to every template.
    """
    if not request.user.is_authenticated:
        return {}
    today = timezone.localdate()
    todays_qs = Expense.objects.filter(date=today)
    total = todays_qs.aggregate(sum=Sum('amount'))['sum'] or 0
    return {
        'today_expense_total': total,
        'today_expenses_list': todays_qs
    }
