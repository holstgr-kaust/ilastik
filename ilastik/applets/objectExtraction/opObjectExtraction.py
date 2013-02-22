import copy
import collections

import numpy
import vigra.analysis

from lazyflow.graph import Operator, InputSlot, OutputSlot, OperatorWrapper
from lazyflow.stype import Opaque
from lazyflow.rtype import SubRegion, List
from lazyflow.operators import OpCachedLabelImage, OpMultiArraySlicer2, OpMultiArrayStacker, OpArrayCache

from ilastik.applets.objectExtraction import config

class OpRegionFeatures3d(Operator):
    """
    Produces region features (i.e. a vigra.analysis.RegionFeatureAccumulator) for a 3d image.
    The image MUST have xyz axes, and is permitted to have t and c axes of dim 1.
    """
    RawVolume = InputSlot()
    LabelVolume = InputSlot()
    
    Output = OutputSlot()
    
    def __init__(self, featureNames, *args, **kwargs):
        super( OpRegionFeatures3d, self ).__init__(*args, **kwargs)
        self._featureNames = featureNames
        
    def setupOutputs(self):
        assert self.LabelVolume.meta.shape == self.RawVolume.meta.shape
        assert self.LabelVolume.meta.axistags == self.RawVolume.meta.axistags

        taggedOutputShape = self.LabelVolume.meta.getTaggedShape()
        if 't' in taggedOutputShape.keys():
            assert taggedOutputShape['t'] == 1
        if 'c' in taggedOutputShape.keys():
            assert taggedOutputShape['c'] == 1
        assert set(taggedOutputShape.keys()) - set('tc') == set('xyz'), "Input volumes must have xyz axes."

        # Remove the spatial dims (keep t and c, if present)
        del taggedOutputShape['x']
        del taggedOutputShape['y']
        del taggedOutputShape['z']

        self.Output.meta.shape = tuple( taggedOutputShape.values() )
        self.Output.meta.axistags = vigra.defaultAxistags( "".join( taggedOutputShape.keys() ) )
        # The features for the entire block (in xyz) are provided for the requested tc coordinates.
        self.Output.meta.dtype = object

    def execute(self, slot, subindex, roi, result):
        assert len(roi.start) == len(roi.stop) == len(self.Output.meta.shape)
        assert slot == self.Output
        
        # Process ENTIRE volume
        rawVolume = self.RawVolume[:].wait()
        labelVolume = self.LabelVolume[:].wait()

        # Convert to 3D (preserve axis order)
        spatialAxes = self.RawVolume.meta.getTaggedShape().keys()
        spatialAxes = filter( lambda k: k in 'xyz', spatialAxes )

        rawVolume = rawVolume.view(vigra.VigraArray)
        rawVolume.axistags = self.RawVolume.meta.axistags
        rawVolume3d = rawVolume.withAxes(*spatialAxes)

        labelVolume = labelVolume.view(vigra.VigraArray)
        labelVolume.axistags = self.LabelVolume.meta.axistags
        labelVolume3d = labelVolume.withAxes(*spatialAxes)

        assert numpy.prod(roi.stop - roi.start) == 1        
        acc = self._extract(rawVolume3d, labelVolume3d)
        result[tuple(roi.start)] = acc
        return result

    def _extract(self, image, labels):
        image = numpy.asarray(image, dtype=numpy.float32)
        labels = numpy.asarray(labels, dtype=numpy.uint32)
        return vigra.analysis.extractRegionFeatures(image,
                                                    labels,
                                                    features=self._featureNames,
                                                    ignoreLabel=0)

    def propagateDirty(self, slot, subindex, roi):
        axes = self.RawVolume.meta.getTaggedShape().keys()
        dirtyStart = collections.OrderedDict( zip( axes, roi.start ) )
        dirtyStop = collections.OrderedDict( zip( axes, roi.stop ) )
        
        # Remove the spatial dims (keep t and c, if present)
        del dirtyStart['x']
        del dirtyStart['y']
        del dirtyStart['z']
            
        del dirtyStop['x']
        del dirtyStop['y']
        del dirtyStop['z']
            
        self.Output.setDirty( dirtyStart.values(), dirtyStop.values() )

class OpRegionFeatures(Operator):
    RawImage = InputSlot()
    LabelImage = InputSlot()
    Output = OutputSlot()

    # Schematic:
    # 
    # RawImage ----> opRawTimeSlicer ----> opRawChannelSlicer -----
    #                                                              \
    # LabelImage --> opLabelTimeSlicer --> opLabelChannelSlicer --> opRegionFeatures3dBlocks --> opChannelStacker --> opTimeStacker -> Output

    def __init__(self, featureNames, *args, **kwargs):
        super( OpRegionFeatures, self ).__init__( *args, **kwargs )

        # Distribute the raw data
        self.opRawTimeSlicer = OpMultiArraySlicer2( parent=self )
        self.opRawTimeSlicer.AxisFlag.setValue('t')
        self.opRawTimeSlicer.Input.connect( self.RawImage )
        assert self.opRawTimeSlicer.Slices.level == 1

        self.opRawChannelSlicer = OperatorWrapper( OpMultiArraySlicer2, parent=self )
        self.opRawChannelSlicer.AxisFlag.setValue( 'c' )
        self.opRawChannelSlicer.Input.connect( self.opRawTimeSlicer.Slices )
        assert self.opRawChannelSlicer.Slices.level == 2

        # Distribute the labels
        self.opLabelTimeSlicer = OpMultiArraySlicer2( parent=self )
        self.opLabelTimeSlicer.AxisFlag.setValue('t')
        self.opLabelTimeSlicer.Input.connect( self.LabelImage )
        assert self.opLabelTimeSlicer.Slices.level == 1

        self.opLabelChannelSlicer = OperatorWrapper( OpMultiArraySlicer2, parent=self )
        self.opLabelChannelSlicer.AxisFlag.setValue( 'c' )
        self.opLabelChannelSlicer.Input.connect( self.opLabelTimeSlicer.Slices )
        assert self.opLabelChannelSlicer.Slices.level == 2
        
        class OpWrappedRegionFeatures3d(Operator):
            """
            This quick hack is necessary because there's not currently a way to wrap an OperatorWrapper.
            We need to double-wrap OpRegionFeatures3d, so we need this operator to provide the first level of wrapping.
            """
            RawVolume = InputSlot(level=1)
            LabelVolume = InputSlot(level=1)
            Output = OutputSlot(level=1)

            def __init__(self, featureNames, *args, **kwargs):
                super( OpWrappedRegionFeatures3d, self ).__init__( *args, **kwargs )
                self._innerOperator = OperatorWrapper( OpRegionFeatures3d, operator_args=[featureNames], parent=self )
                self._innerOperator.RawVolume.connect( self.RawVolume )
                self._innerOperator.LabelVolume.connect( self.LabelVolume )
                self.Output.connect( self._innerOperator.Output )
            
            def setupOutputs(self):
                pass
        
            def execute(self, slot, subindex, roi, destination):
                assert False, "Shouldn't get here."
    
            def propagateDirty(self, slot, subindex, roi):
                pass # Nothing to do...

        # Wrap OpRegionFeatures3d TWICE.
        self.opRegionFeatures3dBlocks = OperatorWrapper( OpWrappedRegionFeatures3d, operator_args=[featureNames], parent=self )
        assert self.opRegionFeatures3dBlocks.RawVolume.level == 2
        assert self.opRegionFeatures3dBlocks.LabelVolume.level == 2
        self.opRegionFeatures3dBlocks.RawVolume.connect( self.opRawChannelSlicer.Slices )
        self.opRegionFeatures3dBlocks.LabelVolume.connect( self.opLabelChannelSlicer.Slices )

        assert self.opRegionFeatures3dBlocks.Output.level == 2
        self.opChannelStacker = OperatorWrapper( OpMultiArrayStacker, parent=self )
        self.opChannelStacker.AxisFlag.setValue('c')

        assert self.opChannelStacker.Images.level == 2
        self.opChannelStacker.Images.connect( self.opRegionFeatures3dBlocks.Output )

        self.opTimeStacker = OpMultiArrayStacker( parent=self )
        self.opTimeStacker.AxisFlag.setValue('t')

        assert self.opChannelStacker.Output.level == 1
        assert self.opTimeStacker.Images.level == 1
        self.opTimeStacker.Images.connect( self.opChannelStacker.Output )

        # Connect our outputs
        self.Output.connect( self.opTimeStacker.Output )
    
    def setupOutputs(self):
        pass
        
    def execute(self, slot, subindex, roi, destination):
        assert False, "Shouldn't get here."
    
    def propagateDirty(self, slot, subindex, roi):
        pass # Nothing to do...

class OpCachedRegionFeatures(Operator):
    RawImage = InputSlot()
    LabelImage = InputSlot()
    Output = OutputSlot(stype=Opaque, rtype=List)

    # Schematic:
    #
    # RawImage -----   blockshape=(t,c)=(1,1)
    #               \                        \
    # LabelImage ----> OpRegionFeatures ----> OpArrayCache --> Output

    def __init__(self, featureNames, *args, **kwargs):
        super(OpCachedRegionFeatures, self).__init__(*args, **kwargs)
        
        # Hook up the labeler
        self._opRegionFeatures = OpRegionFeatures(featureNames, parent=self )
        self._opRegionFeatures.RawImage.connect( self.RawImage )
        self._opRegionFeatures.LabelImage.connect( self.LabelImage )

        # Hook up the cache.
        self._opCache = OpArrayCache( parent=self )
        self._opCache.Input.connect( self._opRegionFeatures.Output )
        
        # Hook up our output slot
        self.Output.connect( self._opCache.Output )
    
    def setupOutputs(self):
        assert self.LabelImage.meta.shape == self.RawImage.meta.shape
        assert self.LabelImage.meta.axistags == self.RawImage.meta.axistags

        # Every value in the regionfeatures output is cached seperately as it's own "block"
        blockshape = (1,) * len( self._opRegionFeatures.Output.meta.shape )
        self._opCache.blockShape.setValue( blockshape )

    def execute(self, slot, subindex, roi, destination):
        assert False, "Shouldn't get here."
    
    def propagateDirty(self, slot, subindex, roi):
        pass # Nothing to do...

class OpAdaptTimeListRoi(Operator):
    """
    Adapts the tc array output from OpRegionFeatures to an Output slot that is called with a 
    'List' rtype, where the roi is a list of time slices, and the output is a 
    dict-of-lists (dict by time, list by channels).
    """
    Input = InputSlot()
    Output = OutputSlot(stype=Opaque, rtype=List)
    
    def setupOutputs(self):
        # Number of time steps
        self.Output.meta.shape = self.Input.meta.getTaggedShape()['t']
        self.Output.meta.dtype = object
    
    def execute(self, slot, subindex, roi, destination):
        assert slot == self.Output, "Unknown output slot"
        taggedShape = self.Input.meta.getTaggedShape()
        numChannels = taggedShape['c']
        channelIndex = taggedShape.keys().index('c')

        # Special case: An empty roi list means "request everything"
        if len(roi) == 0:
            roi = range( taggedShape['t'] )

        taggedShape['t'] = 1
        timeIndex = taggedShape.keys().index('t')
        
        result = {}
        for t in roi:
            result[t] = []
            start = [0] * len(taggedShape)
            stop = taggedShape.values()
            start[timeIndex] = t
            stop[timeIndex] = 1
            a = self.Input(start, stop).wait()
            # Result is provided as a list of arrays by channel
            channelResults = numpy.split(a, numChannels, channelIndex)
            for channelResult in channelResults:
                # Extract from 1x1 ndarray
                result[t].append( channelResult.flat[0] )
        return result

    def propagateDirty(self, slot, subindex, roi):
        assert slot == self.Input
        timeIndex = self.Input.meta.axistags.index('t')
        self.Output.setDirty( List(self.Output, range(roi.start[timeIndex], roi.stop[timeIndex])) )

class OpObjectCenterImage(Operator):
    """A cross in the center of each connected component."""
    BinaryImage = InputSlot()
    RegionCenters = InputSlot(rtype=List)
    Output = OutputSlot()

    def setupOutputs(self):
        self.Output.meta.assignFrom(self.BinaryImage.meta)

    @staticmethod
    def __contained_in_subregion(roi, coords):
        b = True
        for i in range(len(coords)):
            b = b and (roi.start[i] <= coords[i] and coords[i] < roi.stop[i])
        return b

    @staticmethod
    def __make_key(roi, coords):
        key = [coords[i] - roi.start[i] for i in range(len(roi.start))]
        return tuple(key)

    def execute(self, slot, subindex, roi, result):
        assert slot == self.Output, "Unknown output slot"
        result[:] = 0
        for t in range(roi.start[0], roi.stop[0]):
            centers = self.RegionCenters([t]).wait()
            for ch in range(roi.start[-1], roi.stop[-1]):
                centers = centers[t][ch]['RegionCenter']
                centers = numpy.asarray(centers, dtype=numpy.uint32)
                if centers.size:
                    centers = centers[1:,:]
                for center in centers:
                    x, y, z = center[0:3]
                    for dim in (1, 2, 3):
                        for offset in (-1, 0, 1):
                            c = [t, x, y, z, ch]
                            c[dim] += offset
                            c = tuple(c)
                            if self.__contained_in_subregion(roi, c):
                                result[self.__make_key(roi, c)] = 255
        return result

    def propagateDirty(self, slot, subindex, roi):
        if slot is self.RegionCenters:
            self.Output.setDirty(slice(None))


class OpObjectExtraction(Operator):
    name = "Object Extraction"

    RawImage = InputSlot()
    BinaryImage = InputSlot()
    BackgroundLabels = InputSlot()

    LabelImage = OutputSlot()
    ObjectCenterImage = OutputSlot()
    RegionFeatures = OutputSlot(stype=Opaque, rtype=List)

    # these features are needed by classification applet.
    default_features = [
        'RegionCenter',
        'Coord<Minimum>',
        'Coord<Maximum>',
    ]

    # Schematic:
    #
    # BackgroundLabels              LabelImage
    #                 \            /
    # BinaryImage ---> opLabelImage ---> opRegFeats ---> opRegFeatsAdaptOutput ---> RegionFeatures
    #                                   /                                     \
    # RawImage--------------------------                      BinaryImage ---> opObjectCenterImage --> ObjectCenterImage

    def __init__(self, *args, **kwargs):

        super(OpObjectExtraction, self).__init__(*args, **kwargs)

        features = list(set(config.vigra_features).union(set(self.default_features)))

        # internal operators
        self._opLabelImage = OpCachedLabelImage(parent=self)
        self._opRegFeats = OpCachedRegionFeatures(features, parent=self)
        self._opRegFeatsAdaptOutput = OpAdaptTimeListRoi(parent=self)
        self._opObjectCenterImage = OpObjectCenterImage(parent=self)

        # connect internal operators
        self._opLabelImage.Input.connect(self.BinaryImage)
        self._opLabelImage.BackgroundLabels.connect(self.BackgroundLabels)

        self._opRegFeats.RawImage.connect(self.RawImage)
        self._opRegFeats.LabelImage.connect(self._opLabelImage.Output)

        self._opRegFeatsAdaptOutput.Input.connect(self._opRegFeats.Output)
        
        self._opObjectCenterImage.BinaryImage.connect(self.BinaryImage)
        self._opObjectCenterImage.RegionCenters.connect(self._opRegFeatsAdaptOutput.Output)

        # connect outputs
        self.LabelImage.connect(self._opLabelImage.Output)
        self.ObjectCenterImage.connect(self._opObjectCenterImage.Output)
        self.RegionFeatures.connect(self._opRegFeatsAdaptOutput.Output)

    def setupOutputs(self):
        pass

    def execute(self, slot, subindex, roi, result):
        assert False, "Shouldn't get here."

    def propagateDirty(self, inputSlot, subindex, roi):
        pass
