# IMPORTACIONES NECESARIAS ----------------------------------------------------------------------------------------------------

from numpy import select
import pandas as pd
import re

import cx_Oracle as oracle
import win32com.client

import sys
from datetime import *
from datetime import date
from datetime import timedelta

import locale
locale.setlocale(locale.LC_TIME,'es_ES.utf8')

import warnings
warnings.filterwarnings('ignore')


# CONEXION A DATOS -------------------------------------------------------------------------------------------------------------

dsn_tns = oracle.makedsn('******', '******', service_name='OBI500')
conn = oracle.connect(user='*********', password='********', dsn=dsn_tns)
c = conn.cursor()

query="""SELECT NUMERO_SOLICITUD,
            PROPIETARIO_INCIDENTE,
            NOMBRE_CLIENTE,
            ESTADO_SOLICITUD,
            EXTERNAL_ATTRIBUTE_6 as DEFECTO_INICIAL,
            FECHA_CREACION_SOLICITUD,
            FECHA_SOLUCION,
            EXTERNAL_ATTRIBUTE_3 as FECHA_RECOGIDA,
            TIPO_SOLICITUD,
            SUBTIPO_SOLICITUD
       FROM ods_pdn.ODS_CRM_Quimicos 
       WHERE (TIPO_SOLICITUD LIKE 'ADC - RECLAMO%' OR TIPO_SOLICITUD = 'ADC - QUEJA') AND  
             ESTADO_SOLICITUD NOT LIKE 'CANCELADO' AND
             ESTADO_SOLICITUD NOT LIKE 'CERRADO ADC' AND
             FECHA_CREACION_SOLICITUD >= TO_DATE('2021-01-01','RRRR-MM-DD') 
       ORDER BY PROPIETARIO_INCIDENTE, NUMERO_SOLICITUD"""

quejas_reclamos = pd.read_sql(con=conn,sql=query)
quejas_reclamos['ESTADO_SOLICITUD'] = quejas_reclamos['ESTADO_SOLICITUD'].str.strip('.')
quejas_reclamos['FECHA_RECOGIDA'] = pd.to_datetime(quejas_reclamos['FECHA_RECOGIDA'])


# Cargar y agregar la segmentacion
segmentacion = pd.read_excel(r'segmentacion.xlsx')
quejas_reclamos = quejas_reclamos.merge(segmentacion, how= 'left', left_on='NOMBRE_CLIENTE', right_on='Cliente')

# Cargar la BD de Correos
correos = pd.read_csv('bd_correos.csv', sep = ";")


# RECLAMOS --------------------------------------------------------------------------------------------------------------------------

reclamos = quejas_reclamos[quejas_reclamos.TIPO_SOLICITUD != "ADC - QUEJA"]

# Separar por los dos tipos de casos contemplados 
subtipos_1 = ['ADC - CALIDAD/DESEMPENO', 'ADC - OTROS ERRORES', 'ADC - CADENA DE SUMINISTRO']
subtipos_2 = ['ADC - DEVOLUCIONES INMEDIATAS (LOGISTICA)','ADC - ERRORES HUMANOS']

caso1 = reclamos[reclamos.SUBTIPO_SOLICITUD.apply(lambda x: x in subtipos_1)]
caso2 = reclamos[reclamos.SUBTIPO_SOLICITUD.apply(lambda x: x in subtipos_2)]

# Para el Tipo 1
caso1['FECHA ESPERADA CIERRE IDEAL'] = caso1['FECHA_CREACION_SOLICITUD'] + timedelta(days=22)
caso1['FECHA ESPERADA SOLUCION'] = caso1['FECHA_CREACION_SOLICITUD'] + timedelta(days=12)
caso1['FECHA ESPERADA RECOGIDA'] = caso1['FECHA_SOLUCION'] + timedelta(days=8)
caso1['FECHA ESPERADA CIERRE'] = caso1['FECHA_RECOGIDA'] + timedelta(days=2)

# Para el tipo 2
caso2['FECHA ESPERADA CIERRE IDEAL'] = caso2['FECHA_CREACION_SOLICITUD'] + timedelta(days=12)
caso2['FECHA ESPERADA SOLUCION'] = caso2['FECHA_CREACION_SOLICITUD'] + timedelta(days=2)
caso2['FECHA ESPERADA RECOGIDA'] = caso2['FECHA_SOLUCION'] + timedelta(days=8)
caso2['FECHA ESPERADA CIERRE'] = caso2['FECHA_RECOGIDA'] + timedelta(days=2)


# QUEJAS -----------------------------------------------------------------------------------------------------------------------------

quejas = quejas_reclamos[quejas_reclamos.TIPO_SOLICITUD == "ADC - QUEJA"]
quejas['FECHA ESPERADA CIERRE'] = quejas['FECHA_CREACION_SOLICITUD'] + timedelta(days=12)
try:
    quejas['Días para Cierre'] = (quejas['FECHA ESPERADA CIERRE'] - date.today()).days
except:
    quejas['Días para Cierre'] = 0


# FUNCIONES ----------------------------------------------------------------------------------------------------------------------------

def obtener_fechas_reclamos(caso):

    """Obtiene Dias para Solucion, columna que se debe reportar y otras.
       Recibe un DF y lo modifica"""

    caso.reset_index(inplace=True, drop=True)

    # DIAS PARA SOLUCION EN EL AREA EN CUESTION, ASUME QUE LA PERSONA ES LA INDICADA
    for i in range(caso.shape[0]):

        if caso['ESTADO_SOLICITUD'].iloc[i] == 'ABIERTO ADC' or \
        caso['ESTADO_SOLICITUD'].iloc[i] == 'POR SOLUCION ADC':             # Si no lo han solucionado, está en el primer paso
            
            # Tienes x dias para solucionar, primer dueño. Si da negativo, esta atrasado en esta área.
            caso.loc[i,'Días para Solución'] = (caso['FECHA ESPERADA SOLUCION'].iloc[i].date() - date.today()).days
            caso.loc[i,'Columna Fecha a Reportar'] = 'FECHA ESPERADA SOLUCION'

        elif caso['ESTADO_SOLICITUD'].iloc[i] != 'ABIERTO ADC' and \
            caso['ESTADO_SOLICITUD'].iloc[i] != 'POR SOLUCION ADC':           # Si ya lo solucionaron en el primer paso
        
                # Aqui debería chequear que no este ya recogido, pero la fecha de recogida no esta confiable y no hay estado que diga tampoco
                if pd.isnull(caso['FECHA_RECOGIDA'].iloc[i]):                            # y no tiene fecha de recogida, está en logistica

                    # Tienes x dias para solucionar, logistica. Si da negativo, esta atrasado en esta área.
                    caso.loc[i,'Días para Solución'] = (caso['FECHA ESPERADA RECOGIDA'].iloc[i].date() - date.today()).days                      
                    caso.loc[i,'Columna Fecha a Reportar'] = 'FECHA ESPERADA RECOGIDA'

                elif ~pd.isnull(caso['FECHA_RECOGIDA'].iloc[i]):                           # Si ya tiene fecha de recogida, está en SAC

                    # Tienes x dias para solucionar, SAC. Si da negativo, esta atrasado en esta área. Nunca deberia ser mayor a 2.
                    caso.loc[i,'Días para Solución'] = (caso['FECHA ESPERADA CIERRE'].iloc[i].date() - date.today()).days
                    caso.loc[i,'Columna Fecha a Reportar'] = 'FECHA ESPERADA CIERRE'

                    if caso.loc[i,'Días para Solución'] < -1000:
                        caso.loc[i,'Días para Solución'] = (caso['FECHA_SOLUCION'].iloc[i].date() + timedelta(days=2) - date.today()).days
                        caso.loc[i,'Columna Fecha a Reportar'] = 'FECHA ESPERADA CIERRE IDEAL'
            
            
        # Encima, cuanto lleva abierto. Si da mas de 22 está retrasado pa todo el mundo.
        caso.loc[i,'Dias Desde Creacion'] = (date.today()-caso['FECHA_CREACION_SOLICITUD'].iloc[i].date()).days

        # Recuerda que igual deberia cerrar el FECHA ESPERADA CIERRE IDEAL en ____ días, si da negativo está atrasado
        caso.loc[i,'Dias para Cierre'] = (caso['FECHA ESPERADA CIERRE IDEAL'].iloc[i].date() - date.today()).days 
    
    return caso

def generar_html_reclamos(casos):

    """Para un df con los casos de una persona, genera el asunto y el HTML del correo"""

    casos.sort_values(by = 'Días para Solución', ascending=False, inplace=True)
    
    dias_para_sln = int(casos['Días para Solución'].tolist()[-1])
    nombre = casos['PROPIETARIO_INCIDENTE'].tolist()[0].rsplit(',', 1)[1].strip().title()

    #ASUNTO

    if casos.shape[0] == 1:

        if dias_para_sln < 0: 
            asunto = f"¡{nombre} tienes un CRM asignado, atrasado por {-dias_para_sln} días!"
        elif dias_para_sln == 0: 
            asunto = f"¡{nombre} tienes un CRM asignado, debes solucionarlo hoy!"
        else: 
            asunto = f"¡{nombre} tienes un CRM asignado, tienes {dias_para_sln} días para solucionarlo!"

    else:

        if dias_para_sln < 0: 
            asunto = f"¡{nombre} tienes CRMs asignados, atrasados hasta por {-dias_para_sln} días!"
        elif dias_para_sln == 0: 
            asunto = f"¡{nombre} tienes CRMs asignados, algunos debes solucionarlos hoy!"
        else: 
            asunto = f"¡{nombre} tienes CRMs asignados, tienes {dias_para_sln} días para solucionarlos!"

    # CUERPO

    header = 'data:image/png;base64,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'

    body = f"""\
    <!DOCTYPE html>
    <html>

    <head>  
            <link href='https://fonts.googleapis.com/css2?family=Nanum+Gothic&display=swap' rel='stylesheet' type='text/css'>
            <link href='https://fonts.googleapis.com/css2?family=Lobster&display=swap' rel='stylesheet' type='text/css'>
        <style type="text/css">
            span {{
                font-style: oblique;
                font-family: 'Nanum Gothic', sans-serif;
                font-size: 1.5em;
                color: #0E0E0E;
                }}

            .dataframe {{
                table-layout: fixed;
                width: 85%;
                height: 35vh;
                border: 0;
                margin-left: auto;
                margin-right: auto;
            }}

            tbody {{
                text-align: center;
                padding: .5em;
                font-size: 1.2em;
                font-weight: bold;
                font-family: 'Nanum Gothic', sans-serif;
                color: #185192;
            }}

            thead {{
                background: #84b343;
                font-weight: bold;
                font-size: 1.3em;
                color: white;
                border: 0;
            }}

            thead th,
            tbody td,
            tbody th {{
                border:0;
                border-radius: .4em;
            }}

        </style>
    </head>    
    
        <body>
            <div>
                <center> <img alt="" src="{header}" width="500" height="179"/> </center>
                <p style="font-family: Helvetica; text-align: center;">
                    <span style="font-family: 'Nanum Gothic', Helvetica, Arial, sans-serif !important;">"""

    if casos.shape[0] == 1:

        body = body + f"""¡Buenos días <font color="#84b343">{nombre}!</font><br>Te recordamos que tienes un caso abierto en la plataforma del CRM.<br><br>
            Este es el detalle de tu caso:</p></span>"""

    else:

        body = body + f"""¡Buenos días <font color="#84b343">{nombre}!</font><br>Te recordamos que tienes <font color="#ce3f34" size="4">{str(int(casos.shape[0]))} casos abiertos</font> en la plataforma del CRM.<br><br>
            Estos son los detalles de tus casos:</p></span>"""

    for caso in casos.index:

        fila = casos[casos.index == caso]
        dias_para_sln_caso = int(fila['Días para Solución'].tolist()[0])
        dias_para_cierre_caso = int(fila['Dias para Cierre'].tolist()[0])
        fecha_cierre = fila['FECHA ESPERADA CIERRE IDEAL'].tolist()[0].strftime("%d") + ' de ' + fila['FECHA ESPERADA CIERRE IDEAL'].tolist()[0].strftime("%B")

        if (dias_para_sln_caso < 0) & (dias_para_cierre_caso < 0):
             parte_uno = f'<span><b>La embarramos con toda</b>, todas las áreas involucradas han incumplido. Vemos un retraso en tu área de {-dias_para_sln_caso} días, para no decepcionar más al cliente necesitamos movernos.<br><b>Por favor, debes darle solución a este caso HOY<br><font color="#84b343">¡Pilas pues!</font></b></span><br><br>'

        #     parte_uno = f"<span><b>Este caso está atrasado en tu área por {-dias_para_sln_caso} días, "
        #     parte_dos = f"""y deberíamos haber dado respuesta al cliente el {fecha_cierre},  
        #                     hace {-dias_para_cierre_caso} días</b></span><br><br>"""

        elif (dias_para_sln_caso < 0) & (dias_para_cierre_caso >= 0):
            parte_uno = f'<span><b>Vemos un retraso en tu área de {-dias_para_sln_caso} días</b> para responder a este reclamo. Aún podemos cumplirle al cliente, para no decepcionarlo necesitamos movernos<br><b>Por favor, debes darle solución a este caso HOY<br><font color="#84b343">¡Pilas pues!</font></b></span><br><br>'

        # elif (dias_para_sln_caso < 0) & (dias_para_cierre_caso == 0):
        #     parte_uno = f"<span><b>Este caso está atrasado en tu área por {-dias_para_sln_caso} días, "
        #     parte_dos = f"y el cliente está esperando nuestra respuesta HOY</b></span><br><br>"
        
        #elif (dias_para_sln_caso < 0) & (dias_para_cierre_caso > 0):
        #     parte_uno = f"<span><b>Este caso está atrasado en tu área por {-dias_para_sln_caso} días, "
        #     parte_dos = f"""y tenemos el compromiso de dar respuesta al cliente el {fecha_cierre}, 
        #                     en {dias_para_cierre_caso} días</b></span><br><br>"""

        elif (dias_para_sln_caso == 0):
            parte_uno = f'<span><b>Sólo tienes el día de HOY</b> para darle respuesta a este reclamo.<br>Necesitamos movernos para darle respuesta al cliente.<br><b><font color="#84b343">¡No la embarres!</font></b></span><br><br>'

        #elif (dias_para_sln_caso == 0) & (dias_para_cierre_caso < 0):
        #     parte_uno = f"<span><b>Debes solucionar este caso hoy en tu área, "
        #     parte_dos = f"""y deberíamos haber dado respuesta al cliente el {fecha_cierre},  
        #                     hace {-dias_para_cierre_caso} días</b></span><br><br>"""

        # elif (dias_para_sln_caso == 0) & (dias_para_cierre_caso == 0):
        #     parte_uno = f"<span><b>Debes solucionar este caso hoy en tu área, "
        #     parte_dos = f"y el cliente está esperando nuestra respuesta HOY</b></span><br><br>"
        
        # elif (dias_para_sln_caso == 0) & (dias_para_cierre_caso > 0):
        #     parte_uno = f"<span><b>Debes solucionar este caso hoy en tu área, "
        #     parte_dos = f"""y tenemos el compromiso de dar respuesta al cliente el {fecha_cierre}, 
        #                     en {dias_para_cierre_caso} días</b></span><br><br>"""

        elif (dias_para_sln_caso > 0) & (dias_para_cierre_caso < 0):
            parte_uno = f'<span><b>Ya la embarramos </b>con el cliente porque en otras áreas hemos presentado retrasos, para no decepcionarlo más necesitamos movernos.<br>Tienes máximo {-dias_para_cierre_caso} días para solucionar, el {fecha_cierre}<br><b><font color="#84b343">¡Pilas pues!</font></b></span><br><br>'

        #     parte_uno = f"<span><b>Tienes {dias_para_sln_caso} días para solucionar este caso en tu área, "
        #     parte_dos = f"""pero por favor recuerda que deberíamos haber dado respuesta al cliente el {fecha_cierre}, 
        #                     hace {-dias_para_cierre_caso} días</b></span><br><br>"""

        elif (dias_para_sln_caso > 0) & (dias_para_cierre_caso == 0):
            parte_uno = f'<span>Este reclamo llegó tarde a tus manos por retrasos en otras áreas, para no decepcionar más al cliente necesitamos de tu ayuda.<br><b>Tienes máximo {dias_para_sln_caso} días para solucionar</b>, el {fecha_cierre}<br><b><font color="#84b343">¡Pilas pues!</font></b> </span><br><br>'

        #     parte_uno = f"<span><b>Tienes {dias_para_sln_caso} días para solucionar este caso en tu área, "
        #     parte_dos = f"pero por favor recuerda que el cliente está esperando nuestra respuesta HOY</b></span><br><br>"
        
        elif (dias_para_sln_caso > 0) & (dias_para_cierre_caso > 0):
            parte_uno = f'<span><b>Vamos muy bien </b>resolviendo este reclamo.<br>Tienes {dias_para_sln_caso} días para cumplirle al cliente, el caso se debe cerrar el {dias_para_sln_caso}<br><b><font color="#84b343">¡Vamos por el WOW!</font> </b></span><br><br>'

        #     parte_uno = f"<span><b>Tienes {dias_para_sln_caso} días para solucionar este caso en tu área, "
        #     parte_dos = f"""pero por favor recuerda que tenemos el compromiso de dar respuesta al cliente el {fecha_cierre}, 
        #                     en {dias_para_cierre_caso} días</b></span><br><br>"""

        tabla = fila[['NUMERO_SOLICITUD', 
                      'ESTADO_SOLICITUD', 
                      'Cliente',
                      'Segmento',
                      'DEFECTO_INICIAL',
                      'FECHA_CREACION_SOLICITUD',
                      'Días para Solución',
                      fila['Columna Fecha a Reportar'].tolist()[0]]]
        tabla.rename(columns = {
                    'NUMERO_SOLICITUD': 'Número Solicitud',
                    'ESTADO_SOLICITUD': 'Estado Solicitud',
                    'DEFECTO_INICIAL': 'Defecto Inicial',
                    'FECHA_CREACION_SOLICITUD': 'Fecha de Creación',
                    fila['Columna Fecha a Reportar'].tolist()[0]: fila['Columna Fecha a Reportar'].tolist()[0].title()
                        }, inplace = True)

        body = body + f"""<center>{parte_uno}<br>

                       <div style="padding:10px 10px 500px 500px; display: flex; align-items: center; justify-content: center;">
                        {tabla.to_html(
                            index=False,
                            classes=['dataframe', 'tbody', 'thead'],
                            border=0,
                            col_space='20vw')}                     
                        <br><br></div></center>"""
        
        # if esta en esta lista de personas de logistica
            # ponerles "recuerda que debes poner la fecha exacta de recogida"
    
    body = body + '''
        <p style="font-family: Helvetica; text-align: center;">
            <span>Gracias por tu compromiso, con tu gestión lograremos dar respuesta al cliente<br>
                    Saludos</span></p>
    </div>
    </body>
    </html>
    '''

    return asunto, body

def enviar_correo(correo_propietario, asunto, body):

    outlook = win32com.client.Dispatch('outlook.application')

    mail = outlook.CreateItem(0)
    mail.To = correo_propietario
    mail.Subject = asunto
    mail.display()
    bodystart = re.search("<body.*?>", mail.HTMLBody)
    mail.HTMLBody = re.sub(bodystart.group(), 
                           bodystart.group() + body,
                           mail.HTMLBody)
    mail.Send()

def generar_html_quejas(casos):
    
    casos.sort_values(by = 'FECHA ESPERADA CIERRE', ascending=False, inplace=True)
    
    dias_para_cierre = int( (casos['FECHA ESPERADA CIERRE'].tolist()[-1] - date.today()).days )
    nombre = casos['PROPIETARIO_INCIDENTE'].tolist()[0].rsplit(',', 1)[1].strip().title()
    
    #ASUNTO

    if casos.shape[0] == 1:

        if dias_para_cierre < 0: 
            asunto = f"¡{nombre} tienes un CRM asignado, atrasado por {-dias_para_cierre} días!"
        elif dias_para_cierre == 0: 
            asunto = f"¡{nombre} tienes un CRM asignado, debes solucionarlo hoy!"
        else: 
            asunto = f"¡{nombre} tienes un CRM asignado, tienes {dias_para_cierre} días para solucionarlo!"

    else:

        if dias_para_cierre < 0: 
            asunto = f"¡{nombre} tienes CRMs asignados, atrasados hasta por {-dias_para_cierre} días!"
        elif dias_para_cierre == 0: 
            asunto = f"¡{nombre} tienes CRMs asignados, algunos debes solucionarlos hoy!"
        else: 
            asunto = f"¡{nombre} tienes CRMs asignados, tienes {dias_para_cierre} días para solucionarlos!"

    # CUERPO

    if casos.shape[0] == 1:

        body = f"""\
        <html>
        <head></head>
        <body>
            Buenos días {nombre},<br> Te recordamos que tienes un caso abierto en la plataforma del CRM.<br><br>
            Este es el detalle de tu caso:</p>"""

    else:

        body = f"""\
        <html>
        <head></head>
        <body>
            Buenos días {nombre},<br> Te recordamos que tienes varios casos abiertos en la plataforma del CRM.<br><br>
            Estos son los detalles de tus casos:</p>"""

    
    for caso in casos.index:

        fila = casos[casos.index == caso]
        dias_para_cierre_caso = int( (fila['FECHA ESPERADA CIERRE'].tolist()[0] - date.today()).days )
        fecha_cierre = fila['FECHA ESPERADA CIERRE'].tolist()[0].strftime("%d/%m/%Y")

        if (dias_para_cierre_caso < 0):
            parte_uno = f"<b>Este caso está atrasado en tu área por {-dias_para_cierre_caso} días, deberíamos haber dado respuesta al cliente el {fecha_cierre}</b><br><br>"

        elif (dias_para_cierre_caso == 0):
            parte_uno = f"<b>Debes solucionar este caso hoy, el cliente está esperando nuestra respuesta</b><br><br>"
        
        elif (dias_para_cierre_caso > 0):
            parte_uno = f"<b>Tienes {dias_para_cierre_caso} días para solucionar este caso, tenemos el compromiso de dar respuesta al cliente el {fecha_cierre}"
        
        tabla = fila[['NUMERO_SOLICITUD','Cliente','Segmento','DEFECTO_INICIAL','FECHA_CREACION_SOLICITUD','Días para Cierre']]

        tabla.rename(columns = {
                    'NUMERO_SOLICITUD': 'Número Solicitud',
                    'DEFECTO_INICIAL': 'Defecto Inicial',
                    'FECHA_CREACION_SOLICITUD': 'Fecha de Creación'
                        }, inplace = True)

        body = body + f"{parte_uno}<br>{tabla.to_html(index=False)}</p>"
    
    body = body + "Gracias por tu compromiso, con tu gestión lograremos dar respuesta al cliente</p>Saludos"""

    return asunto, body

# EJECUCIÓN ---------------------------------------------------------------------------------------------------------------------------------

for df in [caso1, caso2]:

    if df.shape[0]==0:
        continue

    else:

        for propietario in df['PROPIETARIO_INCIDENTE'].unique():

            if propietario == None:
                continue

            else:

                casos = df[df['PROPIETARIO_INCIDENTE'] == propietario]
                casos = obtener_fechas_reclamos(casos)
                asunto, body = generar_html_reclamos(casos)                

                try:
                    correo_propietario = correos[correos.NOMBRE == propietario].CORREO.tolist()[0]
                    enviar_correo(correo_propietario='ana.morales@andercol.com.co; pilar.mejia@andercol.com.co', asunto=asunto, body=body)
                    print(f"Enviado lo de {propietario} a {correo_propietario}")
                except:
                    print(f"Falta el correo de {propietario} en la Base de Datos\nNo se le envió el informe esta vez, por favor agréguelo.")
                    continue



if quejas.shape[0] != 0:

    for propietario in quejas['PROPIETARIO_INCIDENTE'].unique():

            if propietario == None:
                continue

            else:

                casos = quejas[quejas['PROPIETARIO_INCIDENTE'] == propietario]
                asunto, body = generar_html_quejas(casos)

                try:
                    correo_propietario = correos[correos.NOMBRE == propietario].CORREO.tolist()[0]
                    enviar_correo(correo_propietario='******', asunto=asunto, body=body)
                except:
                    print(f"Falta el correo de {propietario} en la Base de Datos\nNo se le envió el informe esta vez, por favor agréguelo.")
                    continue
