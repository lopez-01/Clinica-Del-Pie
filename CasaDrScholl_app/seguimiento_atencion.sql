/*
  Extiende el esquema existente para vincular citas de seguimiento y atenciones
  con su cita. Es seguro volver a ejecutar este script.
*/
IF COL_LENGTH('dbo.Citas', 'ID_Cita_Anterior') IS NULL
BEGIN
    ALTER TABLE dbo.Citas ADD ID_Cita_Anterior INT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_Citas_CitaAnterior' AND parent_object_id = OBJECT_ID('dbo.Citas')
)
BEGIN
    ALTER TABLE dbo.Citas WITH CHECK
    ADD CONSTRAINT FK_Citas_CitaAnterior
        FOREIGN KEY (ID_Cita_Anterior) REFERENCES dbo.Citas(ID_Cita);
END;

IF COL_LENGTH('dbo.ExpedienteClinico', 'ID_Cita') IS NULL
BEGIN
    ALTER TABLE dbo.ExpedienteClinico ADD ID_Cita INT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_ExpedienteClinico_Cita' AND parent_object_id = OBJECT_ID('dbo.ExpedienteClinico')
)
BEGIN
    ALTER TABLE dbo.ExpedienteClinico WITH CHECK
    ADD CONSTRAINT FK_ExpedienteClinico_Cita
        FOREIGN KEY (ID_Cita) REFERENCES dbo.Citas(ID_Cita);
END;

IF COL_LENGTH('dbo.ExpedienteClinico', 'Observaciones') IS NULL
BEGIN
    ALTER TABLE dbo.ExpedienteClinico ADD Observaciones NVARCHAR(500) NULL;
END;
