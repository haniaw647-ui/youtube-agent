from abc import ABC, abstractmethod


class WhatsAppProvider(ABC):
    @abstractmethod
    async def send_template_message(self, to: str, template_name: str, params: list[str]) -> dict:
        """Sends a pre-approved WhatsApp message template with positional
        {{1}}, {{2}}, ... parameters filled from `params`. Returns the API
        response (contains the message id used to correlate delivery-status
        webhooks back to this send)."""
        raise NotImplementedError
