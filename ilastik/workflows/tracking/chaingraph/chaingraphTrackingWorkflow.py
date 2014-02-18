# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# Copyright 2011-2014, the ilastik developers

from lazyflow.graph import Graph
from ilastik.workflow import Workflow
from ilastik.applets.dataSelection import DataSelectionApplet
from ilastik.applets.objectExtraction import ObjectExtractionApplet
from ilastik.applets.tracking.chaingraph.chaingraphTrackingApplet import ChaingraphTrackingApplet
from ilastik.applets.thresholdTwoLevels.thresholdTwoLevelsApplet import ThresholdTwoLevelsApplet
from lazyflow.operators.opReorderAxes import OpReorderAxes
from ilastik.applets.tracking.base.trackingBaseDataExportApplet import TrackingBaseDataExportApplet


class ChaingraphTrackingWorkflow( Workflow ):
    workflowName = "Automatic Tracking Workflow (Chaingraph)"

    def __init__( self, shell, headless, workflow_cmdline_args, *args, **kwargs ):
        graph = kwargs['graph'] if 'graph' in kwargs else Graph()
        if 'graph' in kwargs: del kwargs['graph']
        super(ChaingraphTrackingWorkflow, self).__init__(shell, headless, graph=graph, *args, **kwargs)
        data_instructions = 'Use the "Raw Data" tab to load your intensity image(s).\n\n'\
                            'Use the "Prediction Maps" tab to load your pixel-wise probability image(s).'
        ## Create applets 
        self.dataSelectionApplet = DataSelectionApplet(self,
                                                       "Input Data",
                                                       "Input Data",
                                                       batchDataGui=False,
                                                       force5d=True,
                                                       instructionText=data_instructions,
                                                       max_lanes=1 )

        opDataSelection = self.dataSelectionApplet.topLevelOperator
        opDataSelection.DatasetRoles.setValue( ['Raw Data', 'Prediction Maps'] )
                
        self.thresholdTwoLevelsApplet = ThresholdTwoLevelsApplet( self, 
                                                                  "Threshold and Size Filter", 
                                                                  "ThresholdTwoLevels" )
        
        self.objectExtractionApplet = ObjectExtractionApplet( name="Object Feature Computation",
                                                              workflow=self, interactive=False )
        
        self.trackingApplet = ChaingraphTrackingApplet( workflow=self )
        
        self.dataExportApplet = TrackingBaseDataExportApplet(self, "Tracking Result Export")
        
        self._applets = []                
        self._applets.append(self.dataSelectionApplet)
        self._applets.append(self.thresholdTwoLevelsApplet)
        self._applets.append(self.objectExtractionApplet)
        self._applets.append(self.trackingApplet)
        self._applets.append(self.dataExportApplet)
        
    
    @property
    def applets(self):
        return self._applets

    @property
    def imageNameListSlot(self):
        return self.dataSelectionApplet.topLevelOperator.ImageName
    
    def connectLane( self, laneIndex ):
        opData = self.dataSelectionApplet.topLevelOperator.getLane(laneIndex)
        opTwoLevelThreshold = self.thresholdTwoLevelsApplet.topLevelOperator.getLane(laneIndex)
        opObjExtraction = self.objectExtractionApplet.topLevelOperator.getLane(laneIndex)
        opTracking = self.trackingApplet.topLevelOperator.getLane(laneIndex)
        opDataExport = self.dataExportApplet.topLevelOperator.getLane(laneIndex)
                
        ## Connect operators ##
        op5Raw = OpReorderAxes(parent=self)
        op5Raw.AxisOrder.setValue("txyzc")
        op5Raw.Input.connect(opData.ImageGroup[0])
        
        opTwoLevelThreshold.InputImage.connect( opData.ImageGroup[1] )
        opTwoLevelThreshold.RawInput.connect( opData.ImageGroup[0] ) # Used for display only
        
        # Use OpReorderAxes for both input datasets such that they are guaranteed to 
        # have the same axis order after thresholding
        op5Binary = OpReorderAxes( parent=self )     
        op5Binary.AxisOrder.setValue("txyzc")           
        op5Binary.Input.connect( opTwoLevelThreshold.CachedOutput )
        
        opObjExtraction.RawImage.connect( op5Raw.Output )
        opObjExtraction.BinaryImage.connect( op5Binary.Output )
        
        opTracking.RawImage.connect( op5Raw.Output )
        opTracking.LabelImage.connect( opObjExtraction.LabelImage )
        opTracking.ObjectFeatures.connect( opObjExtraction.RegionFeatures )        
        
        opDataExport.WorkingDirectory.connect( self.dataSelectionApplet.topLevelOperator.WorkingDirectory )
        opDataExport.RawData.connect( op5Raw.Output )
        opDataExport.Input.connect( opTracking.Output )
        opDataExport.RawDatasetInfo.connect( opData.DatasetGroup[0] )
        
    def _inputReady(self, nRoles):
        slot = self.dataSelectionApplet.topLevelOperator.ImageGroup
        if len(slot) > 0:
            input_ready = True
            for sub in slot:
                input_ready = input_ready and \
                    all([sub[i].ready() for i in range(nRoles)])
        else:
            input_ready = False

        return input_ready

    def handleAppletStateUpdateRequested(self):
        """
        Overridden from Workflow base class
        Called when an applet has fired the :py:attr:`Applet.statusUpdateSignal`
        """
        # If no data, nothing else is ready.        
        input_ready = self._inputReady(2) and not self.dataSelectionApplet.busy
        
        opThresholding = self.thresholdTwoLevelsApplet.topLevelOperator
        thresholdingOutput = opThresholding.CachedOutput
        thresholding_ready = input_ready and \
                       len(thresholdingOutput) > 0 

        opObjectExtraction = self.objectExtractionApplet.topLevelOperator
        objectExtractionOutput = opObjectExtraction.ComputedFeatureNames
        features_ready = thresholding_ready and \
                         len(objectExtractionOutput) > 0

        opTracking = self.trackingApplet.topLevelOperator
        tracking_ready = features_ready and \
                           len(opTracking.EventsVector) > 0 and \
                           opTracking.EventsVector.ready() and \
                           opTracking.Output.ready() 
        
        busy = False
        busy |= self.dataSelectionApplet.busy
        busy |= self.dataExportApplet.busy
        busy |= self.trackingApplet.busy        
        self._shell.enableProjectChanges( not busy )
        
        self._shell.setAppletEnabled(self.dataSelectionApplet, not busy)
        self._shell.setAppletEnabled(self.thresholdTwoLevelsApplet, input_ready and not busy)
        self._shell.setAppletEnabled(self.objectExtractionApplet, thresholding_ready and not busy)        
        self._shell.setAppletEnabled(self.trackingApplet, features_ready and not busy)
        self._shell.setAppletEnabled(self.dataExportApplet, tracking_ready and not busy)
