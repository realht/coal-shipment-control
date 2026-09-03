from django.contrib import admin

from .models import CatalogValue

# Legacy-модели AutoBase/AutoCoalGrade/RailCoalGrade (таблицы catalog_auto_bases,
# catalog_auto_coal_grades, catalog_rail_coal_grades) в admin намеренно НЕ
# регистрируются: это исторические мёртвые таблицы, рабочий код их не читает.
# Регистрация вводила в заблуждение — правка уходила «в никуда» (V17-MED-10).
# Сами модели не удаляем (принцип проекта: не удалять исторические таблицы).


@admin.register(CatalogValue)
class CatalogValueAdmin(admin.ModelAdmin):
    """Только для аварийного просмотра из /admin/. Вся правка справочников —
    через кастомный UI /catalogs/ (там аудит, нормализация отгрузок, проверки)."""

    list_display = ["catalog_type", "name", "is_active"]
    list_filter = ["catalog_type", "is_active"]
    search_fields = ["name", "catalog_type"]
    readonly_fields = ["catalog_type", "name", "is_active"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
