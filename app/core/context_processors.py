def user_flags(request):
    if not request.user.is_authenticated:
        return {
            "can_view_auto": False,
            "can_view_rail": False,
            "can_view_duplicates": False,
            "can_view_audit": False,
            "can_import_shipments": False,
            "can_manage_catalogs": False,
            "can_manage_field_settings": False,
            "can_manage_users": False,
            "can_view_system": False,
        }
    can_view_auto = request.user.has_perm("shipments_auto.view_autoshipment")
    can_view_rail = request.user.has_perm("shipments_rail.view_railshipment")
    return {
        "can_view_auto": can_view_auto,
        "can_view_rail": can_view_rail,
        "can_view_duplicates": can_view_auto or can_view_rail,
        "can_view_audit": request.user.has_perm("audit.view_auditlog"),
        "can_import_shipments": request.user.has_perm("imports.import_shipments"),
        "can_manage_catalogs": request.user.has_perm("catalogs.view_catalogvalue"),
        "can_manage_field_settings": request.user.has_perm("core.view_fieldsettings"),
        "can_manage_users": request.user.has_perm("accounts.view_user"),
        "can_view_system": request.user.has_perm("core.view_system_status"),
    }
