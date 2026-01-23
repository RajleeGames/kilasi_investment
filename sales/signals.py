# sales/signals.py

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import InvoiceItem
@receiver([post_save, post_delete], sender=InvoiceItem)
def update_invoice_totals(sender, instance, **kwargs):
    """
    Whenever an InvoiceItem is saved or deleted, recalculate
    the parent Invoice's totals.
    """
    invoice = instance.invoice
    invoice.calculate_totals()
