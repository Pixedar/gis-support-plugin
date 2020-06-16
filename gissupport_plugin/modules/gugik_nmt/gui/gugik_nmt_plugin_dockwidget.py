# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GugikNmtDockWidget
                                 A QGIS plugin
 opis
 Generated by Plugin Builder: http://g-sherman.github.io/Qgis-Plugin-Builder/
                             -------------------
        begin                : 2019-10-28
        git sha              : $Format:%H$
        copyright            : (C) 2019 by Jakub Skowroński SKNG UAM
        email                : skowronski.jakub97@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import os, csv
import urllib.request
#Nie każdy instalator QGIS ma wbudowanego matplotliba, a bibliotek zewnętrznych nie można instalować
# dla wtyczek w oficjalnym repo
# https://github.com/gis-support/gis-support-plugin/issues/4
try:
    from matplotlib import pyplot as plt 
    matplotlib_library = True
except ImportError:
    matplotlib_library = False

from qgis.PyQt import QtGui, uic
from qgis.PyQt.QtWidgets import QDockWidget, QInputDialog, QFileDialog
from qgis.PyQt.QtCore import pyqtSignal, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.core import (QgsMapLayerProxyModel, QgsField, Qgis, QgsTask, QgsApplication,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsVectorLayer, 
    QgsFeature, QgsWkbTypes, QgsGeometry, QgsExpression, QgsFeatureRequest)
from qgis.utils import iface

from ..tools import IdentifyTool, ProfileTool
from .info_dialog import InfoDialog

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'gugik_nmt_plugin_dockwidget_base.ui'))


class GugikNmtDockWidget(QDockWidget, FORM_CLASS):

    closingPlugin = pyqtSignal()
    on_message = pyqtSignal(str, object, int)

    def __init__(self, parent=None):
        """Constructor."""
        super(GugikNmtDockWidget, self).__init__(parent)
        self.setupUi(self)

        self.cbLayers.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.menageSignals()
        self.registerTools()
        self.setButtonIcons()
        #Referencje
        self.savedFeats = []
        self.infoDialog = InfoDialog()

    def setButtonIcons(self):
        """ Ustawienie ikonek dla przycisków """
        self.tbGetPoint.setIcon(QIcon(':/plugins/gissupport_plugin/gugik_nmt/index.svg'))
        self.tbExportCsv.setIcon(QgsApplication.getThemeIcon('mActionAddTable.svg'))
        self.tbCreateTempLyr.setIcon(QgsApplication.getThemeIcon('mActionFileSave.svg'))
        self.tbExtendLayer.setIcon(QgsApplication.getThemeIcon('mActionStart.svg'))
        self.tbMakeLine.setIcon(QgsApplication.getThemeIcon('mActionAddPolyline.svg'))
        self.tbShowProfile.setIcon(QgsApplication.getThemeIcon('mActionAddImage.svg'))
        self.tbResetPoints.setIcon(QgsApplication.getThemeIcon('mIconDelete.svg'))

    def menageSignals(self):
        """ Zarządzanie sygnałami """
        #Customowe sygnały
        self.on_message.connect(self.showMessage)
        #Kontrolki
        self.cbLayers.layerChanged.connect(self.cbLayerChanged)
        self.tbExtendLayer.clicked.connect(self.extendLayerByHeight)
        self.cbxUpdateField.stateChanged.connect(self.switchFieldsCb)
        self.tbCreateTempLyr.clicked.connect(self.createTempLayer)
        self.tbExportCsv.clicked.connect(self.exportToCsv)
        self.tbShowProfile.clicked.connect(self.generatePlot)
        self.tbInfos.clicked.connect(self.showInfo)
        self.tbResetPoints.clicked.connect(lambda: self.identifyTool.reset())

    def registerTools(self):
        """ Zarejestrowanie narzędzi jak narzędzi mapy QGIS """
        self.identifyTool = IdentifyTool(self)
        self.identifyTool.setButton(self.tbGetPoint)
        self.tbGetPoint.clicked.connect(lambda: self.activateTool(self.identifyTool))
        self.profileTool = ProfileTool(self)
        self.profileTool.setButton(self.tbMakeLine)
        self.tbMakeLine.clicked.connect(lambda: self.activateTool(self.profileTool))

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()
        
    def showInfo(self):
        self.infoDialog.show()
    
    def showMessage(self, message, level, time=5):
        """ Wyświetlanie wiadomości na pasku """
        iface.messageBar().pushMessage('Narzędzie GUGiK NMT:', message, level, time)

    def switchFieldsCb(self, state):
        """ Aktualizowanie comboboxa z polami """
        self.cbFields.setEnabled(state)
        self.cbFields.clear()
        layer = self.cbLayers.currentLayer()
        if not layer:
            return
        if not state:
            return
        self.cbFields.addItems([fname for fname in layer.fields().names()])

    def cbLayerChanged(self):
        """ Reagowanie na zmianę warstwy w QgsMapCombobox """
        self.cbxUpdateField.setChecked(False)
        self.cbFields.clear()

    def getSingleHeight(self, geom):
        """ Wysłanie zapytania do serwisu GUGiK NMT po wysokość w podanych współrzędnych """
        # http://services.gugik.gov.pl/nmt/?request=GetHbyXY&x=486617&y=637928
        project_crs = QgsProject.instance().crs().authid()
        point = self.transformGeometry(geom, project_crs).asPoint()
        x, y = point.y(), point.x()
        try:
            r = urllib.request.urlopen(f'https://services.gugik.gov.pl/nmt/?request=GetHbyXY&x={x}&y={y}')
            return r.read().decode()
        except Exception as e:
            self.on_message.emit(str(e), Qgis.Critical, 5)
            return

    def getPointsHeights(self, feats_meta):
        """ 
        Pobieranie wysokości dla większej ilości punktów. Jeśli ich liczba > 200 -
        lista zostaje podzielona na mniejsze częśći i dopiero dla tych części wysyłane są
        requesty do api
        """
        if isinstance(feats_meta, dict):
            feats_meta = list(feats_meta.keys())
        if len(feats_meta) <= 200:
            url = 'https://services.gugik.gov.pl/nmt/?request=GetHByPointList&list=%s'%','.join(feats_meta)
            try:
                r = urllib.request.urlopen(url)
                return r.read().decode()
            except Exception as e:
                self.on_message.emit(str(e), Qgis.Critical, 5)
                return
        else:
            chunks = [feats_meta[i:i + 200] for i in range(0, len(feats_meta), 200)]
            responses = []
            for chunk in chunks:
                url = 'https://services.gugik.gov.pl/nmt/?request=GetHByPointList&list=%s'%','.join(chunk)
                try:
                    r = urllib.request.urlopen(url)
                    responses.append(f'{r.read().decode()}')
                except Exception as e:
                    self.on_message.emit(str(e), Qgis.Critical, 5)
                    return
            responses = ','.join(responses)
            return responses

    def transformGeometry(self, geometry, current_crs, dest_crs='EPSG:2180', multi=False):
        """ Transformacja geometrii """
        if current_crs != dest_crs:
            ct = QgsCoordinateTransform(
                QgsCoordinateReferenceSystem(current_crs), 
                QgsCoordinateReferenceSystem(dest_crs), 
                QgsProject().instance()
                )
            geometry.transform(ct)
        if multi:
            return f'{geometry.asPoint().y()}%20{geometry.asPoint().x()}'
        return geometry

    def createNewField(self, layer):
        """ Utworzenie nowego pola i zwrócenie jego id """
        #Dodanie nowego pola o podanych parametrach
        data_provider = layer.dataProvider()
        data_provider.addAttributes([QgsField('nmt_wys', QVariant.Double)])
        layer.reload()
        #Znalezienie id pola
        field_id = data_provider.fields().indexFromName('nmt_wys')
        return field_id

    def extendLayerByHeight(self):
        """ Rozszerzenie warstwy o pole z wysokością """
        layer = self.cbLayers.currentLayer()
        exp = QgsExpression('num_geometries($geometry) > 1')
        multipart_features = [f for f in layer.getFeatures(QgsFeatureRequest(exp))]
        if multipart_features:
            self.on_message.emit("Rozszerzenie nie jest możliwe, ponieważ warstwa zawiera obiekty o wieloczęściowych geometriach", Qgis.Warning, 5)
        if not layer:
            return
        if self.cbxUpdateField.isChecked():
            field_id = layer.dataProvider().fields().indexFromName(self.cbFields.currentText())
        elif 'nmt_wys' not in layer.fields().names():
            field_id = self.createNewField(layer)
        else:
            field_id = layer.dataProvider().fields().indexFromName('nmt_wys')
        if self.cbxSelectedOnly.isChecked():
            feats = layer.selectedFeatures()
        else:
            feats = list(layer.getFeatures())
        data = {'feats':feats, 'field_id':field_id}
        self.task2 = QgsTask.fromFunction('Dodawanie pola z wysokościa...', self.addHeightToFields, data=data)
        QgsApplication.taskManager().addTask(self.task2)

    def addHeightToFields(self, task: QgsTask, data):
        """ Dodawanie wysokości dla punktów """
        layer = self.cbLayers.currentLayer()
        layer_crs = layer.crs().authid()
        feats_meta = {self.transformGeometry(feat.geometry(), layer_crs, multi=True):feat.id() 
            for feat in data.get('feats')}
        if not feats_meta:
            return
        field_id = data.get('field_id')
        field = layer.dataProvider().fields().field(field_id)
        response = self.getPointsHeights(feats_meta).split(',')
        to_change = {}
        for r in response:
            coords, height = r.replace(' ', '%20', 1).split(' ')
            if field.type() in [QVariant.LongLong, QVariant.Int]:
                height = int(float(height))
            to_change[feats_meta.get(coords)] = {field_id:height}
        layer.dataProvider().changeAttributeValues(to_change)
        self.on_message.emit(f'Pomyślnie dodano pole z wysokościa do warstwy: {layer.name()}', Qgis.Success, 4)
        del self.task2

    def createTempLayer(self):
        """ 
        Tworzenie warstwy tymczasowej i dodanie do niej punktów, 
        dla których zostały pobrane wysokości
        """
        if not self.savedFeats:
            self.on_message.emit('Brak punktów do zapisu', Qgis.Warning, 5)
            return
        text, ok = QInputDialog.getText(self, 'Stwórz warstwę tymczasową', 'Nazwa warstwy:')
        if not ok:
            return
        epsg = QgsProject.instance().crs().authid()
        self.tempLayer = QgsVectorLayer(f'Point?crs={epsg.lower()}&field=id:integer&field=nmt_wys:double', text, 'memory')
        QgsProject.instance().addMapLayer(self.tempLayer)
        self.task = QgsTask.fromFunction('Dodawanie obiektów', self.populateLayer, data=self.savedFeats)
        QgsApplication.taskManager().addTask(self.task)

    def populateLayer(self, task: QgsTask, data):
        """ Dodawanie do wybranej warstwy pobranych wysokości """
        lyr_fields = self.tempLayer.fields()
        total = 100/len(data)
        features = []
        for idx, tempFeat in enumerate(data):
            f = QgsFeature(lyr_fields)
            f.setGeometry(tempFeat.get('geometry'))
            attributes = [idx, tempFeat.get('height')]
            f.setAttributes(attributes)
            features.append(f)
            try:
                self.task.setProgress( idx*total )
            except AttributeError:
                pass
        self.tempLayer.dataProvider().addFeatures(features)
        self.tempLayer.updateExtents(True)
        self.on_message.emit(f'Utworzono warstwę tymczasową: {self.tempLayer.name()}', Qgis.Success, 4)
        self.identifyTool.reset()
        del self.task

    def exportToCsv(self):
        """ Eksport wysokości wraz z interwałami do pliku csv """
        rows = self.twData.rowCount()
        if rows < 1:
            return
        path, _ = QFileDialog.getSaveFileName(filter=f'*.csv')
        if not path:
            return   
        rows = self.twData.rowCount()
        if not path.lower().endswith('.csv'):
            path += '.csv'
        with open(path, 'w') as f:
            writer = csv.writer(f, delimiter=';')
            to_write = [['Odległość', 'Wysokość npm']]
            for row in range(rows):
                if self.twData.item(row, 1):
                    dist = self.twData.item(row, 0).text().replace('.', ',') + 'm'
                    val = self.twData.item(row, 1).text().replace('.', ',')
                    to_write.append([dist, val])
            writer.writerows(to_write)
        self.on_message.emit(f'Wygenerowano plik csv w miejscu: {path}', Qgis.Success, 4)   

    def generatePlot(self):
        """ Wyświetlenie profilu podłużnego """
        if not matplotlib_library:
            self.on_message.emit("Nie wykryto biblioteki matplotlib. W celu prawidłowego działania wyświetalnia profilu proszę ją doinstalować.", Qgis.Warning, 5)
            return
        rows = self.twData.rowCount()
        if rows < 1:
            return
        dist_list = []
        values = []
        for row in range(rows):
            if self.twData.item(row, 1):
                dist = self.twData.item(row, 0).text()
                val = self.twData.item(row, 1).text()
                dist_list.append(float(dist))
                values.append(float(val))
        fig, ax = plt.subplots()
        ax.set(xlabel='Interwał [m]', ylabel='Wysokość npm',
            title='Profil podłużny')
        ax.plot(dist_list, values)
        plt.show()   

    def activateTool(self, tool):
        """ Zmiana aktywnego narzędzia mapy """
        iface.mapCanvas().setMapTool(tool)
        if tool == self.profileTool:
            self.dsbLineLength.setEnabled(True)
