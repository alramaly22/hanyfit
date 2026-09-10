from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from . import currency


@require_POST
def set_currency(request):
    """Manual currency override (site-wide currency switcher).

    Only ever changes the session -- never an order or a payment that
    already happened -- and only ever to a value in the supported-currency
    whitelist (currency.set_currency_override rejects anything else).
    """
    code = (request.POST.get("currency") or "").strip().upper()
    ok = currency.set_currency_override(request, code)

    next_url = request.POST.get("next") or "/"
    # Only ever redirect back into this site: reject anything that isn't a
    # same-site path, including a protocol-relative "//host/..." value,
    # which a browser would otherwise follow off-site.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    if not ok:
        messages.error(request, "That currency is not available.")
    return redirect(next_url)
