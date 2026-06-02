from django.db import models

from userdefinedmodel.basemodels import MetaBase


class UserDefinedModelType(MetaBase):
    name = models.CharField(max_length=200)
    label = models.CharField(max_length=200, blank=True)
    field_config = models.ForeignKey(
        "userdefinedmodel.FieldConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_defined_model_types",
    )
    policies = models.ManyToManyField(
        "userdefinedmodel.Policy",
        through="userdefinedmodel.UserDefinedModelTypePolicy",
        related_name="user_defined_model_types",
        blank=True,
    )

    def __str__(self):
        return self.name
