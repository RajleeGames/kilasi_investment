from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404

from .models import Contact
from .forms import ContactForm

@login_required
def contact_list(request):
    q = (request.GET.get("q") or "").strip()
    role = (request.GET.get("role") or "").strip()

    qs = Contact.objects.all().order_by("-id")

    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(phone__icontains=q) |
            Q(whatsapp__icontains=q) |
            Q(company__icontains=q) |
            Q(email__icontains=q)
        )

    if role:
        qs = qs.filter(role=role)

    page_obj = Paginator(qs, 20).get_page(request.GET.get("page"))

    return render(request, "contacts/contact_list.html", {
        "contacts": page_obj.object_list,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "q": q,
        "role": role,
        "role_choices": Contact.ROLE_CHOICES,
    })


@login_required
def contact_create(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Contact saved successfully.")
            return redirect("contacts:contact_list")
    else:
        form = ContactForm()

    return render(request, "contacts/contact_form.html", {
        "form": form,
        "title": "Add Contact",
    })


@login_required
def contact_update(request, pk):
    obj = get_object_or_404(Contact, pk=pk)

    if request.method == "POST":
        form = ContactForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Contact updated successfully.")
            return redirect("contacts:contact_list")
    else:
        form = ContactForm(instance=obj)

    return render(request, "contacts/contact_form.html", {
        "form": form,
        "title": "Edit Contact",
        "obj": obj,
    })


@login_required
def contact_delete(request, pk):
    obj = get_object_or_404(Contact, pk=pk)

    if request.method == "POST":
        obj.delete()
        messages.success(request, "Contact deleted.")
        return redirect("contacts:contact_list")

    return render(request, "contacts/contact_confirm_delete.html", {"obj": obj})
