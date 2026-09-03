import datetime
from django import forms
from core.field_config import get_entity_config
from catalogs.models import CatalogValue


class CatalogHybridFormMixin:
    """
    Mixin for ModelForms with hybrid catalog/freetext fields.

    Subclasses must define:
        entity: str        — e.g. 'auto_shipment'
        all_fields: list   — all model field names
        labels: dict       — {field_name: display_label}
        date_field: str | None  — field pre-filled with today() on create
    """

    entity: str
    all_fields: list
    labels: dict
    date_field: str | None = None

    CATALOG_SENTINEL = "__other__"

    @staticmethod
    def _lazy_catalog_choices(queryset, blank_label="—"):
        def _choices():
            result = [("", blank_label)]
            result += [(obj.name, obj.name) for obj in queryset]
            result.append(("__other__", "Другое…"))
            return result
        return _choices

    @classmethod
    def _catalog_field_names(cls, cfg):
        return {
            name for name in cls.all_fields
            if cfg.get(name, {}).get("use_catalog", False)
        }

    @classmethod
    def _build_field_lists(cls):
        cfg = get_entity_config(cls.entity)
        catalog_fields = cls._catalog_field_names(cfg)
        main, advanced = [], []
        visible = [
            (name, cfg.get(name, {}))
            for name in cls.all_fields
            if cfg.get(name, {}).get("visible", True)
        ]
        visible.sort(key=lambda x: x[1].get("sort_order", 999))
        for name, field_cfg in visible:
            render_name = f"{name}_select" if name in catalog_fields else name
            if field_cfg.get("section", "main") == "advanced":
                advanced.append(render_name)
            else:
                main.append(render_name)
        return main, advanced

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cfg = get_entity_config(self.entity)
        self._catalog_fields = self._catalog_field_names(cfg)

        for name in list(self.fields):
            if name.endswith("_select") or name.endswith("_other"):
                continue
            field_cfg = cfg.get(name, {})
            if not field_cfg.get("visible", True):
                del self.fields[name]
                continue
            if name in self._catalog_fields:
                self.fields[name].required = False
                self.fields[name].widget = forms.HiddenInput()
            elif field_cfg.get("required", False):
                self.fields[name].required = True
            else:
                self.fields[name].required = False

        if self.date_field and not self.instance.pk and self.date_field in self.fields:
            self.fields[self.date_field].initial = datetime.date.today()

        for field_name in self._catalog_fields:
            if field_name not in cfg:
                continue
            label = cfg[field_name].get("label") or self.labels.get(field_name, field_name)
            ct = f"{self.entity}__{field_name}"
            qs = CatalogValue.objects.filter(catalog_type=ct, is_active=True)

            select_key = f"{field_name}_select"
            other_key = f"{field_name}_other"

            self.fields[select_key] = forms.CharField(
                label=label,
                required=False,
                widget=forms.Select(
                    choices=self._lazy_catalog_choices(qs),
                    attrs={"data-hybrid-select": field_name},
                ),
            )
            self.fields[other_key] = forms.CharField(
                label=f"{label} (свой вариант)",
                required=False,
                widget=forms.TextInput(attrs={
                    "placeholder": f"Введите {label.lower()}",
                    "data-hybrid-other": field_name,
                }),
            )

            if self.instance.pk:
                val = getattr(self.instance, field_name, "") or ""
                known = {obj.name for obj in qs}
                if val and val not in known:
                    self.fields[select_key].initial = self.CATALOG_SENTINEL
                    self.fields[other_key].initial = val
                else:
                    self.fields[select_key].initial = val

    def clean(self):
        cleaned = super().clean()
        cfg = get_entity_config(self.entity)
        for field_name in self._catalog_fields:
            if field_name not in cfg:
                continue
            label = cfg[field_name].get("label") or self.labels.get(field_name, field_name)
            sel = cleaned.get(f"{field_name}_select", "")
            other = cleaned.get(f"{field_name}_other", "").strip()
            if sel == self.CATALOG_SENTINEL:
                if not other:
                    self.add_error(f"{field_name}_other", f"Введите {label.lower()}.")
                else:
                    cleaned[field_name] = other
            elif sel:
                cleaned[field_name] = sel
            else:
                if cfg.get(field_name, {}).get("required", False):
                    self.add_error(f"{field_name}_select", f"Выберите {label.lower()}.")
                else:
                    cleaned[field_name] = ""
        return cleaned

    @classmethod
    def get_main_fields(cls):
        main, _ = cls._build_field_lists()
        return main

    @classmethod
    def get_advanced_fields(cls):
        _, advanced = cls._build_field_lists()
        return advanced
