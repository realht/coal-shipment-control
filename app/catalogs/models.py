from django.db import models


class AutoBase(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_auto_bases"
        verbose_name = "База авто"
        verbose_name_plural = "Базы авто"
        ordering = ["name"]

    def __str__(self):
        return self.name


class AutoCoalGrade(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_auto_coal_grades"
        verbose_name = "Марка угля (авто)"
        verbose_name_plural = "Марки угля (авто)"
        ordering = ["name"]

    def __str__(self):
        return self.name


class RailCoalGrade(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_rail_coal_grades"
        verbose_name = "Марка угля (ЖД)"
        verbose_name_plural = "Марки угля (ЖД)"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CatalogValue(models.Model):
    TYPE_AUTO_GRADE = "auto_shipment__coal_grade"
    TYPE_AUTO_BASE = "auto_shipment__base_code"
    TYPE_RAIL_GRADE = "rail_shipment__cargo"

    catalog_type = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "catalog_values"
        unique_together = [("catalog_type", "name")]
        indexes = [models.Index(fields=["catalog_type"], name="idx_catalog_type")]
        ordering = ["catalog_type", "name"]

    def __str__(self):
        return f"{self.catalog_type}: {self.name}"
