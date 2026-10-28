/*
  Registro detallado de pagos de pacientes.
  Aditivo e idempotente: no modifica ni borra tablas ni registros existentes.
  La aplicación también puede crear esta tabla al abrir el módulo de pagos.
*/
IF OBJECT_ID(N'[dbo].[PagoFactura]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[PagoFactura] (
        [ID_PagoFactura] INT IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_PagoFactura] PRIMARY KEY,
        [ID_Factura] INT NOT NULL,
        [Fecha_Pago] DATETIME2(0) NOT NULL,
        [Monto] DECIMAL(10,2) NOT NULL,
        [ID_MetodoPago] INT NOT NULL,
        CONSTRAINT [CK_PagoFactura_Monto] CHECK ([Monto] > 0),
        CONSTRAINT [FK_PagoFactura_Factura]
            FOREIGN KEY ([ID_Factura]) REFERENCES [dbo].[Factura]([ID_Factura]),
        CONSTRAINT [FK_PagoFactura_MetodoPago]
            FOREIGN KEY ([ID_MetodoPago]) REFERENCES [dbo].[MetodoPago]([ID_MetodoPago])
    );

    CREATE INDEX [IX_PagoFactura_Factura_Fecha]
        ON [dbo].[PagoFactura] ([ID_Factura], [Fecha_Pago]);
END;
