/*
  Registros mensuales de comisiones por personal operativo.
  Script aditivo e idempotente; no modifica los registros existentes.
*/
IF OBJECT_ID(N'[dbo].[Comision]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[Comision] (
        [ID_Comision] INT IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_Comision] PRIMARY KEY,
        [Periodo] NVARCHAR(7) NOT NULL,
        [Monto] DECIMAL(10,2) NOT NULL,
        [Estado] NVARCHAR(20) NOT NULL
            CONSTRAINT [DF_Comision_Estado] DEFAULT N'Calculada',
        [ID_Personal] INT NOT NULL,
        CONSTRAINT [CK_Comision_Monto] CHECK ([Monto] >= 0),
        CONSTRAINT [CK_Comision_Estado]
            CHECK ([Estado] IN (N'Calculada', N'Pendiente', N'Pagada')),
        CONSTRAINT [UQ_Comision_Periodo_Personal]
            UNIQUE ([Periodo], [ID_Personal]),
        CONSTRAINT [FK_Comision_Personal]
            FOREIGN KEY ([ID_Personal]) REFERENCES [dbo].[Personal]([ID_Personal])
    );
END;
