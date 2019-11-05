# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2017, Numenta, Inc.  Unless you have an agreement
# with Numenta, Inc., for a separate license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero Public License for more details.
#
# You should have received a copy of the GNU Affero Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

"""An implementation of TemporalMemory"""

import numpy as np

from htmresearch.support import numpy_helpers as np2
from nupic.bindings.math import Random, SparseMatrixConnections


class ApicalTiebreakTemporalMemory(object):
  """
  A generalized Temporal Memory with apical dendrites that add a "tiebreak".

  Basal connections are used to implement traditional Temporal Memory.

  The apical connections are used for further disambiguation. If multiple cells
  in a minicolumn have active basal segments, each of those cells is predicted,
  unless one of them also has an active apical segment, in which case only the
  cells with active basal and apical segments are predicted.

  In other words, the apical connections have no effect unless the basal input
  is a union of SDRs (e.g. from bursting minicolumns).

  This class is generalized in two ways:

  - This class does not specify when a 'timestep' begins and ends. It exposes
    two main methods: 'depolarizeCells' and 'activateCells', and callers or
    subclasses can introduce the notion of a timestep.
  - This class is unaware of whether its 'basalInput' or 'apicalInput' are from
    internal or external cells. They are just cell numbers. The caller knows
    what these cell numbers mean, but the TemporalMemory doesn't.
  """

  def __init__(self,
               columnCount=2048,
               basalInputSize=0,
               apicalInputSize=0,
               cellsPerColumn=32,
               # TODO unnecessary?
               reducedBasalThreshold=13,
               initialPermanence=0.21,
               # Changed to float
               minThreshold=0.5,
               sampleSize=20,
               noise=0.01,
               learning_rate=0.1,
               # TODO unnecessary?
               basalPredictedSegmentDecrement=0.0,
               # TODO unnecessary?
               apicalPredictedSegmentDecrement=0.0,
               maxSegmentsPerCell=255,
               # TODO check negative one?
               maxSynapsesPerSegment=-1,
               seed=42):
    """
    @param columnCount (int)
    The number of minicolumns

    @param basalInputSize (sequence)
    The number of bits in the basal input

    @param apicalInputSize (int)
    The number of bits in the apical input

    @param cellsPerColumn (int)
    Number of cells per column

    @param reducedBasalThreshold (int)
    The activation threshold of basal (lateral) segments for cells that have
    active apical segments. If equal to activationThreshold (default),
    this parameter has no effect.

    @param initialPermanence (float)
    Initial permanence of a new synapse

    @param minThreshold (int)
    If the number of potential synapses active on a segment is at least this
    threshold, it is said to be "matching" and is eligible for learning.

    @param sampleSize (int)
    How much of the active SDR to sample with synapses.

    @param basalPredictedSegmentDecrement (float)
    Amount by which segments are punished for incorrect predictions.

    @param apicalPredictedSegmentDecrement (float)
    Amount by which segments are punished for incorrect predictions.

    @param maxSynapsesPerSegment
    The maximum number of synapses per segment.

    @param seed (int)
    Seed for the random number generator.
    """

    self.columnCount = columnCount
    self.cellsPerColumn = cellsPerColumn
    self.initialPermanence = initialPermanence
    self.reducedBasalThreshold = reducedBasalThreshold
    self.minThreshold = minThreshold
    self.sampleSize = sampleSize
    self.basalPredictedSegmentDecrement = basalPredictedSegmentDecrement
    self.apicalPredictedSegmentDecrement = apicalPredictedSegmentDecrement
    self.maxSynapsesPerSegment = maxSynapsesPerSegment
    self.maxSegmentsPerCell = maxSegmentsPerCell
    self.basalInputSize = basalInputSize
    self.apicalInputSize = apicalInputSize

    # We use a continuous weight matrix
    # Three dimensional to have weight from every input to every segment
    # +1 for bias term
    self.basalWeigths = np.zeros(
      (self.maxSegmentsPerCell, self.columnCount*self.cellsPerColumn, self.basalInputSize + 1))
    self.apicalWeights = np.zeros(
      (self.maxSegmentsPerCell, self.columnCount*self.cellsPerColumn, self.apicalInputSize + 1))

    self.basalMovingAverages = np.zeros(
      (self.maxSegmentsPerCell, self.columnCount*self.cellsPerColumn, self.basalInputSize + 1))
    self.apicalMovingAverages = np.zeros(
      (self.maxSegmentsPerCell, self.columnCount*self.cellsPerColumn, self.apicalInputSize + 1))

    self.noise = noise
    self.learningRate = learning_rate

    self.rng = Random(seed)
    # Changed already to float64
    self.predictedCells = np.empty(0, dtype="float64")
    self.activeBasalSegments = np.empty(0, dtype="float64")
    self.activeApicalSegments = np.empty(0, dtype="float64")
    self.apicalInput = np.empty(0, dtype="float64")
    self.basalInput = np.empty(0, dtype="float64")
    # Still needs to be changed. However, not clear whether they are necessary
    self.activeCells = np.empty(0, dtype="uint32")
    self.winnerCells = np.empty(0, dtype="uint32")
    self.predictedActiveCells = np.empty(0, dtype="uint32")
    self.matchingBasalSegments = np.empty(0, dtype="uint32")
    self.matchingApicalSegments = np.empty(0, dtype="uint32")
    self.basalPotentialOverlaps = np.empty(0, dtype="int32")
    self.apicalPotentialOverlaps = np.empty(0, dtype="int32")

    # TODO what do these parameters do? Necessary?
    self.useApicalTiebreak=True
    self.useApicalModulationBasalThreshold=True

  def reset(self):
    """
    Clear all cell and segment activity.
    """
    # Changed already to float64
    self.predictedCells = np.empty(0, dtype="float64")
    self.activeBasalSegments = np.empty(0, dtype="float64")
    self.activeApicalSegments = np.empty(0, dtype="float64")
    self.apicalInput = np.empty(0, dtype="float64")
    self.basalInput = np.empty(0, dtype="float64")
    # Still needs to be changed. However, not clear whether they are necessary
    self.activeCells = np.empty(0, dtype="uint32")
    self.winnerCells = np.empty(0, dtype="uint32")
    self.predictedActiveCells = np.empty(0, dtype="uint32")
    self.matchingBasalSegments = np.empty(0, dtype="uint32")
    self.matchingApicalSegments = np.empty(0, dtype="uint32")
    self.basalPotentialOverlaps = np.empty(0, dtype="int32")
    self.apicalPotentialOverlaps = np.empty(0, dtype="int32")

  def depolarizeCells(self, basalInput, apicalInput, learn=None):
    """
    Calculate predictions.

    Depolarization means in this case to calculate the respective probability
    of cells detecting a particular pattern / participating in firing.

    @param basalInput (numpy array)
    List of active input bits for the basal dendrite segments

    @param apicalInput (numpy array)
    List of active input bits for the apical dendrite segments

    @param learn (bool)
    Whether learning is enabled. Some TM implementations may depolarize cells
    differently or do segment activity bookkeeping when learning is enabled.
    """
    activation_apical = self._calculateSegmentActivity(self.apicalWeights, apicalInput, self.minThreshold)
    activation_basal = self._calculateSegmentActivity(self.apicalWeights, basalInput, self.minThreshold)

    predicted_cells = self._calculatePredictedCells(activation_basal, activation_apical)
    normalisation = np.exp(predicted_cells.reshape((self.numberOfColumns(), self.cellsPerColumn))).sum(axis=1)
    predicted_cells = np.exp(predicted_cells) / normalisation
    predicted_cells[predicted_cells < self.minThreshold] = 0.0

    self.predictedCells = predicted_cells
    # TODO necessary to use prediction threshold for apical and basal segment values?
    self.activeBasalSegments = activation_apical
    self.activeApicalSegments = activation_basal
    # Save basal and apical input values for learning
    self.basalInput = basalInput
    self.apicalInput = apicalInput

  def activateCells(self,
                    activeColumns,
                    basalReinforceCandidates,
                    apicalReinforceCandidates,
                    basalGrowthCandidates,
                    apicalGrowthCandidates,
                    learn=True):
    """
    Activate cells in the specified columns, using the result of the previous
    'depolarizeCells' as predictions. Then learn.

    @param activeColumns (numpy array)
    List of active columns

    @param basalReinforceCandidates (numpy array)
    List of bits that the active cells may reinforce basal synapses to.

    @param apicalReinforceCandidates (numpy array)
    List of bits that the active cells may reinforce apical synapses to.

    @param basalGrowthCandidates (numpy array)
    List of bits that the active cells may grow new basal synapses to.

    @param apicalGrowthCandidates (numpy array)
    List of bits that the active cells may grow new apical synapses to

    @param learn (bool)
    Whether to grow / reinforce / punish synapses
    """
    # List of active columns is expected to be an array with indices
    all_columns = np.arange(0, self.numberOfColumns())
    inactive_columns_mask = np.setdiff1d(all_columns, activeColumns)
    correct_predicted_cells = self.predictedCells.copy()
    correct_predicted_cells.reshape(self.numberOfColumns(), self.cellsPerColumn)[inactive_columns_mask, :] = 0
    correct_predicted_cells_mask = correct_predicted_cells[correct_predicted_cells > 0]
    self.activeBasalSegments[:, ~correct_predicted_cells_mask] = 0
    self.activeApicalSegments[:, ~correct_predicted_cells_mask] = 0
    # Calculate learning
    (learningActiveBasalSegments,
     learningMatchingBasalSegments,
     basalSegmentsToPunish,
     newBasalSegmentCells,
     learningCells) = self._calculateBasalLearning(
       activeColumns, burstingColumns, correct_predicted_cells_mask,
       self.activeBasalSegments, self.matchingBasalSegments,
       self.basalPotentialOverlaps)

    (learningActiveApicalSegments,
     learningMatchingApicalSegments,
     apicalSegmentsToPunish,
     newApicalSegmentCells) = self._calculateApicalLearning(
       learningCells, activeColumns, self.activeApicalSegments,
       self.matchingApicalSegments, self.apicalPotentialOverlaps)

    # Learn
    if learn:
      # Learn on existing segments
      for learningSegments in (learningActiveBasalSegments,
                               learningMatchingBasalSegments):
        self._learn(self.basalConnections, self.rng, learningSegments,
                    basalReinforceCandidates, basalGrowthCandidates,
                    self.basalPotentialOverlaps,
                    self.initialPermanence, self.sampleSize,
                    self.permanenceIncrement, self.permanenceDecrement,
                    self.maxSynapsesPerSegment)

      for learningSegments in (learningActiveApicalSegments,
                               learningMatchingApicalSegments):

        self._learn(self.apicalConnections, self.rng, learningSegments,
                    apicalReinforceCandidates, apicalGrowthCandidates,
                    self.apicalPotentialOverlaps, self.initialPermanence,
                    self.sampleSize, self.permanenceIncrement,
                    self.permanenceDecrement, self.maxSynapsesPerSegment)

      # Punish incorrect predictions
      if self.basalPredictedSegmentDecrement != 0.0:
        self.basalConnections.adjustActiveSynapses(
          basalSegmentsToPunish, basalReinforceCandidates,
          -self.basalPredictedSegmentDecrement)

      if self.apicalPredictedSegmentDecrement != 0.0:
        self.apicalConnections.adjustActiveSynapses(
          apicalSegmentsToPunish, apicalReinforceCandidates,
          -self.apicalPredictedSegmentDecrement)

      # Grow new segments
      if len(basalGrowthCandidates) > 0:
        self._learnOnNewSegments(self.basalConnections, self.rng,
                                 newBasalSegmentCells, basalGrowthCandidates,
                                 self.initialPermanence, self.sampleSize,
                                 self.maxSynapsesPerSegment)

      if len(apicalGrowthCandidates) > 0:
        self._learnOnNewSegments(self.apicalConnections, self.rng,
                                 newApicalSegmentCells, apicalGrowthCandidates,
                                 self.initialPermanence, self.sampleSize,
                                 self.maxSynapsesPerSegment)

    # Save the results
    newActiveCells.sort()
    learningCells.sort()
    self.activeCells = newActiveCells
    self.winnerCells = learningCells
    self.predictedActiveCells = correctPredictedCells


  def _calculateBasalLearning(self,
                              activeColumns,
                              burstingColumns,
                              correctPredictedCellsMask,
                              activeBasalSegments,
                              matchingBasalSegments,
                              basalPotentialOverlaps):
    """
    Basic Temporal Memory learning. Correctly predicted cells always have
    active basal segments, and we learn on these segments. In bursting
    columns, we either learn on an existing basal segment, or we grow a new one.

    The only influence apical dendrites have on basal learning is: the apical
    dendrites influence which cells are considered "predicted". So an active
    apical dendrite can prevent some basal segments in active columns from
    learning.

    @param correctPredictedCells (numpy array)
    @param burstingColumns (numpy array)
    @param activeBasalSegments (numpy array)
    @param matchingBasalSegments (numpy array)
    @param basalPotentialOverlaps (numpy array)

    @return (tuple)
    - learningActiveBasalSegments (numpy array)
      Active basal segments on correct predicted cells

    - learningMatchingBasalSegments (numpy array)
      Matching basal segments selected for learning in bursting columns

    - basalSegmentsToPunish (numpy array)
      Basal segments that should be punished for predicting an inactive column

    - newBasalSegmentCells (numpy array)
      Cells in bursting columns that were selected to grow new basal segments

    - learningCells (numpy array)
      Cells that have learning basal segments or are selected to grow a basal
      segment
    """
    # Updating of the moving averages

    noisy_connection_matrix = np.outer(
      (1 - self.noise**2) * activeBasalSegments,
      self.basalInput
    ).reshape(self.maxSegmentsPerCell, self.numberOfCells(), self.basalInputSize) + self.noise**2
    self.basalMovingAverages[:, :, :-1] += self.learningRate * (
            noisy_connection_matrix - self.basalMovingAverages[:, :, :-1]
    )

    noisy_activation_vector = (1 - self.noise) * activeBasalSegments.reshape(-1) + self.noise
    self.basalMovingAverages[:, :, -1].reshape(-1)[:] += self.learningRate * (
            noisy_activation_vector - self.basalMovingAverages[:, :, -1].reshape(-1)
    )

    # --------------------- Not yet updated
    # TODO update to new learning approach
    # Correctly predicted columns
    learningActiveBasalSegments = self.basalConnections.filterSegmentsByCell(
      activeBasalSegments, correctPredictedCells)

    cellsForMatchingBasal = self.basalConnections.mapSegmentsToCells(
      matchingBasalSegments)
    matchingCells = np.unique(cellsForMatchingBasal)

    (matchingCellsInBurstingColumns,
     burstingColumnsWithNoMatch) = np2.setCompare(
       matchingCells, burstingColumns, matchingCells / self.cellsPerColumn,
       rightMinusLeft=True)

    learningMatchingBasalSegments = self._chooseBestSegmentPerColumn(
      self.basalConnections, matchingCellsInBurstingColumns,
      matchingBasalSegments, basalPotentialOverlaps, self.cellsPerColumn)
    newBasalSegmentCells = self._getCellsWithFewestSegments(
      self.basalConnections, self.rng, burstingColumnsWithNoMatch,
      self.cellsPerColumn)

    learningCells = np.concatenate(
      (correctPredictedCells,
       self.basalConnections.mapSegmentsToCells(learningMatchingBasalSegments),
       newBasalSegmentCells))

    # Incorrectly predicted columns
    correctMatchingBasalMask = np.in1d(
      cellsForMatchingBasal / self.cellsPerColumn, activeColumns)

    basalSegmentsToPunish = matchingBasalSegments[~correctMatchingBasalMask]

    return (learningActiveBasalSegments,
            learningMatchingBasalSegments,
            basalSegmentsToPunish,
            newBasalSegmentCells,
            learningCells)


  def _calculateApicalLearning(self,
                               learningCells,
                               activeColumns,
                               activeApicalSegments,
                               matchingApicalSegments,
                               apicalPotentialOverlaps):
    """
    Calculate apical learning for each learning cell.

    The set of learning cells was determined completely from basal segments.
    Do all apical learning on the same cells.

    Learn on any active segments on learning cells. For cells without active
    segments, learn on the best matching segment. For cells without a matching
    segment, grow a new segment.

    @param learningCells (numpy array)
    @param correctPredictedCells (numpy array)
    @param activeApicalSegments (numpy array)
    @param matchingApicalSegments (numpy array)
    @param apicalPotentialOverlaps (numpy array)

    @return (tuple)
    - learningActiveApicalSegments (numpy array)
      Active apical segments on correct predicted cells

    - learningMatchingApicalSegments (numpy array)
      Matching apical segments selected for learning in bursting columns

    - apicalSegmentsToPunish (numpy array)
      Apical segments that should be punished for predicting an inactive column

    - newApicalSegmentCells (numpy array)
      Cells in bursting columns that were selected to grow new apical segments
    """

    # Cells with active apical segments
    learningActiveApicalSegments = self.apicalConnections.filterSegmentsByCell(
      activeApicalSegments, learningCells)

    # Cells with matching apical segments
    learningCellsWithoutActiveApical = np.setdiff1d(
      learningCells,
      self.apicalConnections.mapSegmentsToCells(learningActiveApicalSegments))
    cellsForMatchingApical = self.apicalConnections.mapSegmentsToCells(
      matchingApicalSegments)
    learningCellsWithMatchingApical = np.intersect1d(
      learningCellsWithoutActiveApical, cellsForMatchingApical)
    learningMatchingApicalSegments = self._chooseBestSegmentPerCell(
      self.apicalConnections, learningCellsWithMatchingApical,
      matchingApicalSegments, apicalPotentialOverlaps)

    # Cells that need to grow an apical segment
    newApicalSegmentCells = np.setdiff1d(learningCellsWithoutActiveApical,
                                         learningCellsWithMatchingApical)

    # Incorrectly predicted columns
    correctMatchingApicalMask = np.in1d(
      cellsForMatchingApical / self.cellsPerColumn, activeColumns)

    apicalSegmentsToPunish = matchingApicalSegments[~correctMatchingApicalMask]

    return (learningActiveApicalSegments,
            learningMatchingApicalSegments,
            apicalSegmentsToPunish,
            newApicalSegmentCells)

  @staticmethod
  def _calculateSegmentActivity(weights, activeInput, minThreshold):
    """
    Calculate the active and matching apical segments for this timestep.


    Previous return tuple
    - activeSegments (numpy array)
      Dendrite segments with enough active connected synapses to cause a
      dendritic spike

    - matchingSegments (numpy array)
      Dendrite segments with enough active potential synapses to be selected for
      learning in a bursting column

    - potentialOverlaps (numpy array)
      The number of active potential synapses for each segment.
      Includes counts for active, matching, and nonmatching segments.

    Now, we return instead the values for the depolarised neurons segments only

    :param apicalWeights (numpy array) continuous valued matrix with weights
    :param activeInput (numpy array) input activity values
    IMPORTANT: We assume now that active input is a continuous valued numpy array. This means that all segments
    pass an input.
    :returns depolarised values of the neurons. Representing activity probabilities
    """

    # append 1 to input because of bias term
    activation = np.log(weights.dot(np.append(activeInput, [1])))
    return activation

  def _calculatePredictedCells(self, activeBasalSegments, activeApicalSegments):
    """
    Calculate the predicted cells, given the set of active segments.

    An active basal segment is enough to predict a cell.
    An active apical segment is *not* enough to predict a cell.

    When a cell has both types of segments active, other cells in its minicolumn
    must also have both types of segments to be considered predictive.

    @param activeBasalSegments (numpy array) two dimensional (segments x cells)
    @param activeApicalSegments (numpy array) two dimentsional (segments x cells)

    @return (numpy array)
    """
    max_cells = activeBasalSegments.max(axis=0)
    max_cells += activeApicalSegments.max(axis=0)

    # TODO what does this line do?
    # if self.useApicalTiebreak == False:
    #     predictedCells = cellsForBasalSegments

    return max_cells


  @staticmethod
  def _learn(connections, rng, learningSegments, activeInput, growthCandidates,
             potentialOverlaps, initialPermanence, sampleSize,
             permanenceIncrement, permanenceDecrement, maxSynapsesPerSegment):
    """
    Adjust synapse permanences, grow new synapses, and grow new segments.

    @param learningActiveSegments (numpy array)
    @param learningMatchingSegments (numpy array)
    @param activeInput (numpy array)
    @param growthCandidates (numpy array)
    @param potentialOverlaps (numpy array)
    """

    # Learn on existing segments
    connections.adjustSynapses(learningSegments, activeInput,
                               permanenceIncrement, -permanenceDecrement)

    # Grow new synapses. Calculate "maxNew", the maximum number of synapses to
    # grow per segment. "maxNew" might be a number or it might be a list of
    # numbers.
    if sampleSize == -1:
      maxNew = len(growthCandidates)
    else:
      maxNew = sampleSize - potentialOverlaps[learningSegments]

    if maxSynapsesPerSegment != -1:
      synapseCounts = connections.mapSegmentsToSynapseCounts(
        learningSegments)
      numSynapsesToReachMax = maxSynapsesPerSegment - synapseCounts
      maxNew = np.where(maxNew <= numSynapsesToReachMax,
                        maxNew, numSynapsesToReachMax)

    connections.growSynapsesToSample(learningSegments, growthCandidates,
                                     maxNew, initialPermanence, rng)


  @staticmethod
  def _learnOnNewSegments(connections, rng, newSegmentCells, growthCandidates,
                          initialPermanence, sampleSize, maxSynapsesPerSegment):

    numNewSynapses = len(growthCandidates)

    if sampleSize != -1:
      numNewSynapses = min(numNewSynapses, sampleSize)

    if maxSynapsesPerSegment != -1:
      numNewSynapses = min(numNewSynapses, maxSynapsesPerSegment)

    newSegments = connections.createSegments(newSegmentCells)
    connections.growSynapsesToSample(newSegments, growthCandidates,
                                     numNewSynapses, initialPermanence,
                                     rng)


  @classmethod
  def _chooseBestSegmentPerCell(cls,
                                connections,
                                cells,
                                allMatchingSegments,
                                potentialOverlaps):
    """
    For each specified cell, choose its matching segment with largest number
    of active potential synapses. When there's a tie, the first segment wins.

    @param connections (SparseMatrixConnections)
    @param cells (numpy array)
    @param allMatchingSegments (numpy array)
    @param potentialOverlaps (numpy array)

    @return (numpy array)
    One segment per cell
    """

    candidateSegments = connections.filterSegmentsByCell(allMatchingSegments,
                                                         cells)

    # Narrow it down to one pair per cell.
    onePerCellFilter = np2.argmaxMulti(potentialOverlaps[candidateSegments],
                                       connections.mapSegmentsToCells(
                                         candidateSegments))
    learningSegments = candidateSegments[onePerCellFilter]

    return learningSegments


  @classmethod
  def _chooseBestSegmentPerColumn(cls, connections, matchingCells,
                                  allMatchingSegments, potentialOverlaps,
                                  cellsPerColumn):
    """
    For all the columns covered by 'matchingCells', choose the column's matching
    segment with largest number of active potential synapses. When there's a
    tie, the first segment wins.

    @param connections (SparseMatrixConnections)
    @param matchingCells (numpy array)
    @param allMatchingSegments (numpy array)
    @param potentialOverlaps (numpy array)
    """

    candidateSegments = connections.filterSegmentsByCell(allMatchingSegments,
                                                         matchingCells)

    # Narrow it down to one segment per column.
    cellScores = potentialOverlaps[candidateSegments]
    columnsForCandidates = (connections.mapSegmentsToCells(candidateSegments) /
                            cellsPerColumn)
    onePerColumnFilter = np2.argmaxMulti(cellScores, columnsForCandidates)

    learningSegments = candidateSegments[onePerColumnFilter]

    return learningSegments


  @classmethod
  def _getCellsWithFewestSegments(cls, connections, rng, columns,
                                  cellsPerColumn):
    """
    For each column, get the cell that has the fewest total basal segments.
    Break ties randomly.

    @param connections (SparseMatrixConnections)
    @param rng (Random)
    @param columns (numpy array) Columns to check

    @return (numpy array)
    One cell for each of the provided columns
    """
    candidateCells = np2.getAllCellsInColumns(columns, cellsPerColumn)

    # Arrange the segment counts into one row per minicolumn.
    segmentCounts = np.reshape(connections.getSegmentCounts(candidateCells),
                               newshape=(len(columns),
                                         cellsPerColumn))

    # Filter to just the cells that are tied for fewest in their minicolumn.
    minSegmentCounts = np.amin(segmentCounts, axis=1, keepdims=True)
    candidateCells = candidateCells[np.flatnonzero(segmentCounts ==
                                                   minSegmentCounts)]

    # Filter to one cell per column, choosing randomly from the minimums.
    # To do the random choice, add a random offset to each index in-place, using
    # casting to floor the result.
    (_,
     onePerColumnFilter,
     numCandidatesInColumns) = np.unique(candidateCells / cellsPerColumn,
                                         return_index=True, return_counts=True)

    offsetPercents = np.empty(len(columns), dtype="float32")
    rng.initializeReal32Array(offsetPercents)

    np.add(onePerColumnFilter,
           offsetPercents*numCandidatesInColumns,
           out=onePerColumnFilter,
           casting="unsafe")

    return candidateCells[onePerColumnFilter]


  def getActiveCells(self):
    """
    @return (numpy array)
    Active cells
    """
    return self.activeCells


  def getPredictedActiveCells(self):
    """
    @return (numpy array)
    Active cells that were correctly predicted
    """
    return self.predictedActiveCells


  def getWinnerCells(self):
    """
    @return (numpy array)
    Cells that were selected for learning
    """
    return self.winnerCells


  def getActiveBasalSegments(self):
    """
    @return (numpy array)
    Active basal segments for this timestep
    """
    return self.activeBasalSegments


  def getActiveApicalSegments(self):
    """
    @return (numpy array)
    Matching basal segments for this timestep
    """
    return self.activeApicalSegments


  def numberOfColumns(self):
    """ Returns the number of columns in this layer.

    @return (int) Number of columns
    """
    return self.columnCount


  def numberOfCells(self):
    """
    Returns the number of cells in this layer.

    @return (int) Number of cells
    """
    return self.numberOfColumns() * self.cellsPerColumn


  def getCellsPerColumn(self):
    """
    Returns the number of cells per column.

    @return (int) The number of cells per column.
    """
    return self.cellsPerColumn

  def getReducedBasalThreshold(self):
    """
    Returns the reduced basal activation threshold for apically active cells.
    @return (int) The activation threshold.
    """
    return self.reducedBasalThreshold


  def setReducedBasalThreshold(self, reducedBasalThreshold):
    """
    Sets the reduced basal activation threshold for apically active cells.
    @param reducedBasalThreshold (int) activation threshold.
    """
    self.reducedBasalThreshold = reducedBasalThreshold


  def getInitialPermanence(self):
    """
    Get the initial permanence.
    @return (float) The initial permanence.
    """
    return self.initialPermanence


  def setInitialPermanence(self, initialPermanence):
    """
    Sets the initial permanence.
    @param initialPermanence (float) The initial permanence.
    """
    self.initialPermanence = initialPermanence


  def getMinThreshold(self):
    """
    Returns the min threshold.
    @return (int) The min threshold.
    """
    return self.minThreshold


  def setMinThreshold(self, minThreshold):
    """
    Sets the min threshold.
    @param minThreshold (int) min threshold.
    """
    self.minThreshold = minThreshold


  def getSampleSize(self):
    """
    Gets the sampleSize.
    @return (int)
    """
    return self.sampleSize


  def setSampleSize(self, sampleSize):
    """
    Sets the sampleSize.
    @param sampleSize (int)
    """
    self.sampleSize = sampleSize


  def getPermanenceIncrement(self):
    """
    Get the permanence increment.
    @return (float) The permanence increment.
    """
    return self.permanenceIncrement


  def setPermanenceIncrement(self, permanenceIncrement):
    """
    Sets the permanence increment.
    @param permanenceIncrement (float) The permanence increment.
    """
    self.permanenceIncrement = permanenceIncrement


  def getPermanenceDecrement(self):
    """
    Get the permanence decrement.
    @return (float) The permanence decrement.
    """
    return self.permanenceDecrement


  def setPermanenceDecrement(self, permanenceDecrement):
    """
    Sets the permanence decrement.
    @param permanenceDecrement (float) The permanence decrement.
    """
    self.permanenceDecrement = permanenceDecrement


  def getBasalPredictedSegmentDecrement(self):
    """
    Get the predicted segment decrement.
    @return (float) The predicted segment decrement.
    """
    return self.basalPredictedSegmentDecrement


  def setBasalPredictedSegmentDecrement(self, predictedSegmentDecrement):
    """
    Sets the predicted segment decrement.
    @param predictedSegmentDecrement (float) The predicted segment decrement.
    """
    self.basalPredictedSegmentDecrement = basalPredictedSegmentDecrement


  def getApicalPredictedSegmentDecrement(self):
    """
    Get the predicted segment decrement.
    @return (float) The predicted segment decrement.
    """
    return self.apicalPredictedSegmentDecrement


  def setApicalPredictedSegmentDecrement(self, predictedSegmentDecrement):
    """
    Sets the predicted segment decrement.
    @param predictedSegmentDecrement (float) The predicted segment decrement.
    """
    self.apicalPredictedSegmentDecrement = apicalPredictedSegmentDecrement


  def getConnectedPermanence(self):
    """
    Get the connected permanence.
    @return (float) The connected permanence.
    """
    return self.connectedPermanence


  def setConnectedPermanence(self, connectedPermanence):
    """
    Sets the connected permanence.
    @param connectedPermanence (float) The connected permanence.
    """
    self.connectedPermanence = connectedPermanence


  def getUseApicalTieBreak(self):
    """
    Get whether we actually use apical tie-break.
    @return (Bool) Whether apical tie-break is used.
    """
    return self.useApicalTiebreak


  def setUseApicalTiebreak(self, useApicalTiebreak):
    """
    Sets whether we actually use apical tie-break.
    @param useApicalTiebreak (Bool) Whether apical tie-break is used.
    """
    self.useApicalTiebreak = useApicalTiebreak


  def getUseApicalModulationBasalThreshold(self):
    """
    Get whether we actually use apical modulation of basal threshold.
    @return (Bool) Whether apical modulation is used.
    """
    return self.useApicalModulationBasalThreshold


  def setUseApicalModulationBasalThreshold(self, useApicalModulationBasalThreshold):
    """
    Sets whether we actually use apical modulation of basal threshold.
    @param useApicalModulationBasalThreshold (Bool) Whether apical modulation is used.
    """
    self.useApicalModulationBasalThreshold = useApicalModulationBasalThreshold



class ApicalTiebreakPairMemory(ApicalTiebreakTemporalMemory):
  """
  Pair memory with apical tiebreak.
  """

  def compute(self,
              activeColumns,
              basalInput,
              apicalInput=(),
              basalGrowthCandidates=None,
              apicalGrowthCandidates=None,
              learn=True):
    """
    Perform one timestep. Use the basal and apical input to form a set of
    predictions, then activate the specified columns, then learn.

    @param activeColumns (numpy array)
    List of active columns

    @param basalInput (numpy array)
    List of active input bits for the basal dendrite segments

    @param apicalInput (numpy array)
    List of active input bits for the apical dendrite segments

    @param basalGrowthCandidates (numpy array or None)
    List of bits that the active cells may grow new basal synapses to.
    If None, the basalInput is assumed to be growth candidates.

    @param apicalGrowthCandidates (numpy array or None)
    List of bits that the active cells may grow new apical synapses to
    If None, the apicalInput is assumed to be growth candidates.

    @param learn (bool)
    Whether to grow / reinforce / punish synapses
    """
    activeColumns = np.asarray(activeColumns)
    basalInput = np.asarray(basalInput)
    apicalInput = np.asarray(apicalInput)

    if basalGrowthCandidates is None:
      basalGrowthCandidates = basalInput
    basalGrowthCandidates = np.asarray(basalGrowthCandidates)

    if apicalGrowthCandidates is None:
      apicalGrowthCandidates = apicalInput
    apicalGrowthCandidates = np.asarray(apicalGrowthCandidates)

    self.depolarizeCells(basalInput, apicalInput, learn)
    self.activateCells(activeColumns, basalInput, apicalInput,
                       basalGrowthCandidates, apicalGrowthCandidates, learn)


  def getPredictedCells(self):
    """
    @return (numpy array)
    Cells that were predicted for this timestep
    """
    return self.predictedCells


  def getBasalPredictedCells(self):
    """
    @return (numpy array)
    Cells with active basal segments
    """
    return np.unique(
      self.basalConnections.mapSegmentsToCells(
        self.activeBasalSegments))


  def getApicalPredictedCells(self):
    """
    @return (numpy array)
    Cells with active apical segments
    """
    return np.unique(
      self.apicalConnections.mapSegmentsToCells(
        self.activeApicalSegments))




class ApicalTiebreakSequenceMemory(ApicalTiebreakTemporalMemory):
  """
  Sequence memory with apical tiebreak.
  """

  def __init__(self,
               columnCount=2048,
               apicalInputSize=0,
               cellsPerColumn=32,
               activationThreshold=13,
               reducedBasalThreshold=13,
               initialPermanence=0.21,
               connectedPermanence=0.50,
               minThreshold=10,
               sampleSize=20,
               permanenceIncrement=0.1,
               permanenceDecrement=0.1,
               basalPredictedSegmentDecrement=0.0,
               apicalPredictedSegmentDecrement=0.0,
               maxSynapsesPerSegment=-1,
               seed=42):
    params = {
      "columnCount": columnCount,
      "basalInputSize": columnCount * cellsPerColumn,
      "apicalInputSize": apicalInputSize,
      "cellsPerColumn": cellsPerColumn,
      "activationThreshold": activationThreshold,
      "reducedBasalThreshold": reducedBasalThreshold,
      "initialPermanence": initialPermanence,
      "connectedPermanence": connectedPermanence,
      "minThreshold": minThreshold,
      "sampleSize": sampleSize,
      "permanenceIncrement": permanenceIncrement,
      "permanenceDecrement": permanenceDecrement,
      "basalPredictedSegmentDecrement": basalPredictedSegmentDecrement,
      "apicalPredictedSegmentDecrement": apicalPredictedSegmentDecrement,
      "maxSynapsesPerSegment": maxSynapsesPerSegment,
      "seed": seed,
    }

    super(ApicalTiebreakSequenceMemory, self).__init__(**params)

    self.prevApicalInput = np.empty(0, dtype="uint32")
    self.prevApicalGrowthCandidates = np.empty(0, dtype="uint32")
    self.prevPredictedCells = np.empty(0, dtype="uint32")


  def reset(self):
    """
    Clear all cell and segment activity.
    """
    super(ApicalTiebreakSequenceMemory, self).reset()

    self.prevApicalInput = np.empty(0, dtype="uint32")
    self.prevApicalGrowthCandidates = np.empty(0, dtype="uint32")
    self.prevPredictedCells = np.empty(0, dtype="uint32")


  def compute(self,
              activeColumns,
              apicalInput=(),
              apicalGrowthCandidates=None,
              learn=True):
    """
    Perform one timestep. Activate the specified columns, using the predictions
    from the previous timestep, then learn. Then form a new set of predictions
    using the new active cells and the apicalInput.

    @param activeColumns (numpy array)
    List of active columns

    @param apicalInput (numpy array)
    List of active input bits for the apical dendrite segments

    @param apicalGrowthCandidates (numpy array or None)
    List of bits that the active cells may grow new apical synapses to
    If None, the apicalInput is assumed to be growth candidates.

    @param learn (bool)
    Whether to grow / reinforce / punish synapses
    """
    activeColumns = np.asarray(activeColumns)
    apicalInput = np.asarray(apicalInput)

    if apicalGrowthCandidates is None:
      apicalGrowthCandidates = apicalInput
    apicalGrowthCandidates = np.asarray(apicalGrowthCandidates)

    self.prevPredictedCells = self.predictedCells

    self.activateCells(activeColumns, self.activeCells, self.prevApicalInput,
                       self.winnerCells, self.prevApicalGrowthCandidates, learn)
    self.depolarizeCells(self.activeCells, apicalInput, learn)

    self.prevApicalInput = apicalInput.copy()
    self.prevApicalGrowthCandidates = apicalGrowthCandidates.copy()


  def getPredictedCells(self):
    """
    @return (numpy array)
    The prediction from the previous timestep
    """
    return self.prevPredictedCells


  def getNextPredictedCells(self):
    """
    @return (numpy array)
    The prediction for the next timestep
    """
    return self.predictedCells


  def getNextBasalPredictedCells(self):
    """
    @return (numpy array)
    Cells with active basal segments
    """
    return np.unique(
      self.basalConnections.mapSegmentsToCells(
        self.activeBasalSegments))


  def getNextApicalPredictedCells(self):
    """
    @return (numpy array)
    Cells with active apical segments
    """
    return np.unique(
      self.apicalConnections.mapSegmentsToCells(
        self.activeApicalSegments))
