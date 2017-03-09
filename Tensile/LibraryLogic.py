import os
import os.path
import array
import csv
from sys import stdout

from copy import deepcopy

from Common import print1, print2, printWarning, HR, printExit, defaultAnalysisParameters, globalParameters, pushWorkingPath, popWorkingPath, assignParameterWithDefault
from SolutionStructs import Solution
import YAMLIO

################################################################################
# Analyze Problem Type
################################################################################
def analyzeProblemType( problemTypeTuple, inputParameters ):
  problemType = problemTypeTuple[0]
  problemSizes = problemTypeTuple[1]
  dataFileName = problemTypeTuple[2]
  solutionsFileName = problemTypeTuple[3]
  print2(HR)
  print1("# %s" % problemType)

  ######################################
  # Read Solutions
  (problemSizes, solutions) = YAMLIO.readSolutions(solutionsFileName)
  print2("# ProblemSizes: %s" % problemSizes)
  solutionMinNaming = Solution.getMinNaming(solutions)
  print2("# Solutions:")
  solutionIdx = 0
  for solution in solutions:
    print2("#  (%u) %s" % (solutionIdx, Solution.getNameMin(solution, \
        solutionMinNaming)))
    solutionIdx += 1
  print2(HR)

  ######################################
  # Read Data From CSV
  logicAnalyzer = LogicAnalyzer( \
      problemType, problemSizes, solutions, inputParameters)
  logicAnalyzer.populateFromCSV(dataFileName)

  ######################################
  # Remove invalid solutions
  logicAnalyzer.removeInvalidSolutions()

  ######################################
  # Remove least important solutions
  logicAnalyzer.removeLeastImportantSolutions()

  ######################################
  # Correct outliers
  if inputParameters["SmoothOutliers"]:
    logicAnalyzer.smoothOutliers()
  #logicAnalyzer.print2D([0, 0])

  ######################################
  # Create Rules
  logic = logicAnalyzer.enRule(0, logicAnalyzer.globalIndexRange)
  print2("# Final Logic:")
  print2(logic)
  logicComplexity = [0]*logicAnalyzer.numIndices
  logicAnalyzer.scoreLogicComplexity(logic, logicComplexity)
  print2("Logic Complexity: %s" % logicComplexity)
  score = logicAnalyzer.scoreRangeForLogic( \
      logicAnalyzer.globalIndexRange, logic)
  print1("\nScore: %.0f ms" % (score/1000))

  logicAnalyzer.prepareLogic(logic)

  #return (skinnyRules01, skinnyRules10, diagonalRules)
  #return (problemType, logicAnalyzer.solutionsUsed, [], [], logicAnalyzer.diagonalRules )
  return (problemType, logicAnalyzer.solutions, logicAnalyzer.indexOrder, logic)



################################################################################
# LogicAnalyzer
################################################################################
class LogicAnalyzer:

  ##############################################################################
  ##############################################################################
  ###
  ###  Entry / Top-Level Functions
  ###
  ##############################################################################
  ##############################################################################

  ##############################################################################
  # ENTRY: Init
  ##############################################################################
  def __init__(self, problemType, problemSizes, solutions, inputParameters):
    self.problemType = problemType
    self.problemSizes = problemSizes
    self.parameters = inputParameters
    print2("ProblemSizes: %s" % self.problemSizes)
    # TODO verify that data is symmetric for diagonal
    #if self.problemSizes[self.problemType["Index0"]] \
    #    != self.problemSizes[self.problemType["Index1"]]:
    #  printExit("d0 / d1 must be symmetric for analysis.")
    self.numProblemSizes = problemSizes.numProblemSizes # native order
    print1("NumProblemSizes: %s" % self.numProblemSizes)
    self.numIndices = len(self.numProblemSizes)
    self.solutions = solutions
    self.numSolutions = len(self.solutions)
    self.solutionMinNaming = Solution.getMinNaming(solutions)
    self.solutionNames = []
    self.solutionTiles = []
    for solution in self.solutions:
      self.solutionNames.append(Solution.getNameMin(solution, \
          self.solutionMinNaming))
      self.solutionTiles.append("%ux%u"%(solution["MacroTile0"], solution["MacroTile1"]))
    self.flopsPerMac = self.problemType["DataType"].flopsPerMac()

    # special indices
    self.idx0 = self.problemType["Index0"]
    self.idx1 = self.problemType["Index1"]
    self.idxU = self.problemType["IndexUnroll"]

    # total size of data array
    self.totalProblems = 1
    for numProblems in self.numProblemSizes:
      self.totalProblems *= numProblems
    self.totalSize = self.totalProblems * self.numSolutions
    print2("TotalProblems: %u" % self.totalProblems)
    print2("TotalSolutions: %u" % self.numSolutions)
    print2("TotalSize: %u" % self.totalSize)
    self.data = array.array('f', [0]*self.totalSize)

    # map problem sizes -> index
    self.problemSizeToIndex = []
    self.problemIndexToSize = []
    sizedIdx = 0
    mappedIdx = 0
    for i in range(0, self.numIndices):
      self.problemSizeToIndex.append({})
      self.problemIndexToSize.append([])
      if self.problemSizes.indexIsSized[i]:
        index = self.problemSizes.indicesSized[sizedIdx]
        sizedIdx += 1
      else:
        index = self.problemSizes.indicesSized[ \
          self.problemSizes.indicesMapped[mappedIdx]]
        mappedIdx += 1
      currentSize = index[0]
      currentStride = index[1]
      idx = 0
      while currentSize <= index[3]:
        self.problemSizeToIndex[i][currentSize] = idx
        self.problemIndexToSize[i].append(currentSize)
        currentSize += currentStride
        currentStride += index[2]
        idx += 1
    self.rangeIndicesFree = range(0, self.problemType["NumIndicesC"])
    self.rangeIndicesSummation = range(self.problemType["NumIndicesC"], \
        self.problemType["TotalIndices"])
    self.indexOrder = self.recommendedIndexOrder()
    print2("IndexOrder: %s" % self.indexOrder)
    self.globalIndexRange = []
    for i in range(0, self.numIndices):
      self.globalIndexRange.append([0, self.numProblemSizes[i]])
    self.problemIndicesForGlobalRange \
        = self.problemIndicesForRange(self.globalIndexRange)
    self.tab = [""]*self.numIndices



  ##############################################################################
  # ENTRY: Read In CSV
  ##############################################################################
  def populateFromCSV(self, dataFileName):

    # open file
    try:
      dataFile = open(dataFileName, "r")
    except IOError:
      printExit("Can't open \"%s\" to get data" % dataFileName )

    # column indices
    csvFile = csv.reader(dataFile)
    problemSizeStartIdx = 1
    totalSizeIdx = problemSizeStartIdx + self.numIndices
    solutionStartIdx = totalSizeIdx + 1
    rowLength = solutionStartIdx + self.numSolutions

    # iterate over rows
    rowIdx = 0
    for row in csvFile:
      rowIdx+=1
      if rowIdx == 1:
        continue
      else:
        if len(row) < rowLength:
          printWarning("CSV File %s row %u doesn't have %u elements; ignoring remainer of file." \
              % (dataFileName, rowIdx, rowLength) )
          break

        # get problem size
        problemSize = []
        for i in range(problemSizeStartIdx, totalSizeIdx):
          problemSize.append(int(row[i]))
        problemIndices = []
        for i in range(0, self.numIndices):
          problemIndices.append(self.problemSizeToIndex[i][problemSize[i]])
        serialIdx = self.indicesToSerial(0, problemIndices)

        # data
        solutionIdx = 0
        for i in range(solutionStartIdx, rowLength):
          gflops = float(row[i])
          self.data[serialIdx+solutionIdx] = gflops
          solutionIdx += 1
    if rowIdx < 2:
      printExit("CSV File %s only has %u row(s); prior benchmark must not have run long enough to produce data." \
          % (dataFileName, rowIdx) )


  ##############################################################################
  # ENTRY: Remove Invalid Solutions
  ##############################################################################
  def removeInvalidSolutions(self):
    #problemIndices = [0]*self.numIndices
    allSolutionValid = False
    while not allSolutionValid:
      moreProblems = True
      invalidIdx = -1
      for problemIndices in self.problemIndicesForGlobalRange:
        problemSerial = self.indicesToSerial(0, problemIndices)
        for solutionIdx in range(0, self.numSolutions):
          gflops = self.data[problemSerial+solutionIdx]
          if gflops == 0:
            invalidIdx = solutionIdx
            break
      if invalidIdx >= 0:
        print1("# Removing Invalid Solution: %u %s" \
            % (invalidIdx, self.solutionNames[invalidIdx]) )
        self.removeSolution(invalidIdx)
      else:
        allSolutionValid = True


  ##############################################################################
  # ENTRY: Remove Least Important Solutions
  ##############################################################################
  def removeLeastImportantSolutions(self):
    # Remove least important solutions
    while len(self.solutions) > 1:
      (lisIdx, lisPercSaved, lisPercWins, lisPercExec) \
          = self.leastImportantSolution()
      if lisPercSaved < self.parameters["SolutionImportanceMin"]:
        self.removeSolution(lisIdx)
        continue
      else:
        break


  ##############################################################################
  # ENTRY: Smooth Outliers
  ##############################################################################
  def smoothOutliers(self):
    problemSizes = [0]*self.numIndices
    for problemIndices in self.problemIndicesForGlobalRange:
      problemSerial = self.indicesToSerial(0, problemIndices)

      for solutionIdx in range(0, self.numSolutions):
        gflops = self.data[problemSerial+solutionIdx]
        neighborGFlops = []
        smoothProblem = False
        for iIdx in range(0, self.numIndices):
          if problemIndices[iIdx] > 0 \
              and problemIndices[iIdx] < self.numProblemSizes[iIdx]-1:
            neighborBeforeIndices = deepcopy(problemIndices)
            neighborAfterIndices = deepcopy(problemIndices)
            neighborBeforeIndices[iIdx] -= 1
            neighborAfterIndices[iIdx] += 1
            neighborBeforeIdx = self.indicesToSerial(0, neighborBeforeIndices)
            neighborAfterIdx = self.indicesToSerial(0, neighborAfterIndices)
            neighborBeforeGFlops = self.data[neighborBeforeIdx+solutionIdx]
            neighborAfterGFlops = self.data[neighborAfterIdx+solutionIdx]
            neighborGFlops.append(neighborBeforeGFlops)
            neighborGFlops.append(neighborAfterGFlops)
            if neighborBeforeGFlops > gflops \
                and neighborAfterGFlops < gflops :
              smoothProblem = True
        if smoothProblem:
          s = ""
          for i in range(0, self.numIndices):
            problemSizes[i] = self.problemIndexToSize[i][problemIndices[i]]
            s += "%u, " % problemSizes[i]
          new = sum(neighborGFlops)/len(neighborGFlops)
          old = self.data[problemSerial+solutionIdx]
          s += "%f -> %f" % (old, new)
          self.data[problemSerial+solutionIdx] \
              = sum(neighborGFlops)/len(neighborGFlops)


  ##############################################################################
  # ENTRY: En Rule
  # currentIndexIndex = 0, 1, 2, 3...
  # currentIndexRange will have only 1 size for prior indices (unless initial)
  #
  # Rule:
  # [128, [
  #         [64, [
  #                [16, 0],
  #                [2880,1]
  #              ]
  #         ],
  #         [96, [
  #                [16, 0],
  #                [64, 1]
  #              ]
  #         ]
  #       ]
  # ], another
  #
  #
  ##############################################################################
  def enRule(self, currentIndexIndex, currentIndexRange):
    cii = currentIndexIndex
    if currentIndexIndex == 0:
      self.tab[cii] = "[] "
    elif currentIndexIndex == 1:
      self.tab[cii] = "[%2u] " % ( \
          currentIndexRange[self.indexOrder[0]][0])
    elif currentIndexIndex == 2:
      self.tab[cii] = "[%2u,%2u] " % ( \
          currentIndexRange[self.indexOrder[0]][0], \
          currentIndexRange[self.indexOrder[1]][0])
    elif currentIndexIndex == 3:
      self.tab[cii] = "[%2u,%2u,%2u] " % ( \
          currentIndexRange[self.indexOrder[0]][0], \
          currentIndexRange[self.indexOrder[1]][0], \
          currentIndexRange[self.indexOrder[2]][0])
    elif currentIndexIndex == 4:
      self.tab[cii] = "[%2u,%2u,%2u,%2u] " % ( \
          currentIndexRange[self.indexOrder[0]][0], \
          currentIndexRange[self.indexOrder[1]][0], \
          currentIndexRange[self.indexOrder[2]][0], \
          currentIndexRange[self.indexOrder[3]][0])
    tab = self.tab[cii]
    if globalParameters["PrintLevel"] == 1:
      stdout.write("\n%s"%tab)
    currentIndex = self.indexOrder[currentIndexIndex]
    print2("%senRule(%s)" % (tab, currentIndexRange))
    nextIndexIndex = currentIndexIndex+1
    nextIndexRange = deepcopy(currentIndexRange)
    isLastIndex = currentIndexIndex == self.numIndices-1
    ruleList = []

    ########################################
    # if there's only 1 problem size here
    ########################################
    if currentIndexRange[currentIndex][1] \
        - currentIndexRange[currentIndex][0] == 1:

      ########################################
      # this is last index, so just return fastest solution
      if isLastIndex:
        # TODO optimize b/c this should be only single problem
        #scores = self.scoreRangeForSolutions(currentIndexRange)
        #winnerIdx = 0
        #for solutionIdx in range(1, self.numSolution):
        #  if scores[solutionIdx] < scores[winnerIdx]:
        #    winnerIdx = solutionIdx
        winnerIdx = self.winnerForRange(currentIndexRange)
        #print2("%sreturning early winner=%u" % (tab, winnerIdx))
        ruleList.append(-1)
        ruleList.append(winnerIdx)
        if globalParameters["PrintLevel"] == 1:
          stdout.write("#")

      ########################################
      # this isn't last index, so just recursively return next index
      else:
        #print2("%sreturning early enRule(%s)" \
        #    % (tab, nextIndexRange) )
        rule = [ -1, self.enRule(nextIndexIndex, nextIndexRange) ]
        ruleList.append(rule)
        if globalParameters["PrintLevel"] == 1:
          stdout.write("#")

    ########################################
    # full iterative rule list
    ########################################
    else:

      ########################################
      # create initial rule
      initialSize = min(currentIndexRange[currentIndex][0] \
          + self.parameters["InitialSolutionWindow"], \
          self.numProblemSizes[currentIndex])
      nextIndexRange[currentIndex][1] = initialSize
      if isLastIndex:
        winnerIdx = self.winnerForRange(nextIndexRange)
        initialRule = [ currentIndexRange[currentIndex][0], winnerIdx]
      else:
        #print2("%sinitialRule(%s)" % (tab, nextIndexRange))
        initialRule = [ currentIndexRange[currentIndex][0], \
            self.enRule(nextIndexIndex, nextIndexRange) ]
        #print2("%sinitialRule(%s) DONE" % (tab, nextIndexRange))
      ruleList.append(initialRule)
      if globalParameters["PrintLevel"] == 1:
        stdout.write("#")

      ########################################
      # for all problem indices in this index
      for problemIndex in range(currentIndexRange[currentIndex][0]+1, \
          currentIndexRange[currentIndex][1]):
        nextIndexRange[currentIndex][0] = problemIndex
        nextIndexRange[currentIndex][1] = problemIndex+1
        priorRule = ruleList[len(ruleList)-1]
        priorRuleForSize = deepcopy(priorRule)
        priorRuleForSize[0] = problemIndex

        if isLastIndex:
          winnerIdx = self.winnerForRange(nextIndexRange)
          candidateRule = [ problemIndex, winnerIdx]
        else:
          candidateRule = [ problemIndex, self.enRule(nextIndexIndex, \
              nextIndexRange) ]

        ########################################
        # candidate same as prior
        if candidateRule[1] == priorRule[1]:
          #print2("%sP[%2u]: same" % (tab, problemIndex))
          ruleList[len(ruleList)-1][0] = problemIndex
          if globalParameters["PrintLevel"] == 1:
            stdout.write(" ")
          continue

        ########################################
        # compare candidate vs prior
        else:
          #print2("%sScoring P:%s for Prior=%s, Cand=%s" \
          #    % ( tab, nextIndexRange, priorRuleForSize, candidateRule))
          # score prior
          priorRuleScore = self.scoreRangeForLogic(nextIndexRange, \
              [priorRuleForSize])
          logicComplexity = [0]*self.numIndices
          self.scoreLogicComplexity( \
              [priorRuleForSize], logicComplexity)
          priorRuleScore += self.parameters["BranchPenalty"] \
              * sum(logicComplexity)
          # score candidate
          candidateRuleScore = self.scoreRangeForLogic(nextIndexRange, \
              [candidateRule])
          logicComplexity = [0]*self.numIndices
          self.scoreLogicComplexity( \
              [candidateRule], logicComplexity)
          candidateRuleScore += self.parameters["BranchPenalty"] \
              * sum(logicComplexity)
          candidateRuleScore += self.parameters["BranchPenalty"] # penalize
          candidateFaster = candidateRuleScore < priorRuleScore
          print2("%sP[%2u]: %s %s~%.0fus < %s~%.0fus" % (tab, problemIndex, \
              "wins" if candidateFaster else "same", \
              candidateRule, candidateRuleScore, priorRuleForSize, \
              priorRuleScore ))

          ########################################
          # candidate wins
          if candidateRuleScore < priorRuleScore:
            ruleList.append(candidateRule)
            if globalParameters["PrintLevel"] == 1:
              stdout.write("#")

          ########################################
          # prior wins
          else:
            if globalParameters["PrintLevel"] == 1:
              stdout.write(".")
            ruleList[len(ruleList)-1][0] = problemIndex

    #print2("%sReturning RuleList: %s" % (tab, ruleList))
    return ruleList



  ##############################################################################
  ##############################################################################
  ###
  ###  Mid-Level Functions
  ###
  ##############################################################################
  ##############################################################################



  ##############################################################################
  # Prepare Logic
  # convert threshold indices to sizes
  # last threshold = -1
  ##############################################################################
  def prepareLogic(self, logic):
    depth = self.getLogicDepth(logic)
    if depth == 0: return
    indexIndex = self.numIndices - depth
    index = self.indexOrder[indexIndex]
    for i in range(0, len(logic)):
      if i == len(logic)-1:
        logic[i][0] = -1
      else:
        logic[i][0] = self.problemIndexToSize[index][logic[i][0]]
      self.prepareLogic(logic[i][1])


  ##############################################################################
  # Print2D
  ##############################################################################
  def print2D(self, indices ):
    indicesIdx = 0
    problemIndices = []
    for i in range(0, self.numIndices):
      if i == self.idx0:
        problemIndices.append(-1)
      elif i == self.idx1:
        problemIndices.append(-1)
      else:
        problemIndices.append(indices[indicesIdx])
        indicesIdx += 1

    winnerIndices = []
    w = "winner"
    g = "gflops"
    f = "faster"
    s = "second"
    sss = []
    for sIdx in range(0, self.numSolutions):
      sss.append("Sol[%u]" % sIdx)
    for j in range(0, self.numProblemSizes[1]):
      w += ",%4u" % self.problemIndexToSize[1][j]
      g += ",%4u" % self.problemIndexToSize[1][j]
      f += ",%4u" % self.problemIndexToSize[1][j]
      s += ",%4u" % self.problemIndexToSize[1][j]
      for sIdx in range(0, self.numSolutions):
        sss[sIdx] += ",%4u" % self.problemIndexToSize[1][j]
    w += "\n"
    g += "\n"
    f += "\n"
    s += "\n"
    for sIdx in range(0, self.numSolutions):
      sss[sIdx] += "\n"
    for i in range(0, self.numProblemSizes[0]):
      problemIndices[self.idx0] = i
      w += "%4u" % self.problemIndexToSize[0][i]
      g += "%4u" % self.problemIndexToSize[0][i]
      f += "%4u" % self.problemIndexToSize[0][i]
      s += "%4u" % self.problemIndexToSize[0][i]
      for sIdx in range(0, self.numSolutions):
        sss[sIdx] += "%4u" % self.problemIndexToSize[0][i]
      for j in range(0, self.numProblemSizes[1]):
        problemIndices[self.idx1] = j
        problemSerial = self.indicesToSerial(0, problemIndices)
        for sIdx in range(0, self.numSolutions):
          sss[sIdx] += ",%f" % self.data[problemSerial+sIdx]

        if self.data[problemSerial+0] > self.data[problemSerial+1]:
          winnerIdx = 0
          winnerGFlops = self.data[problemSerial+0]
          secondIdx = 1
          secondGFlops = self.data[problemSerial+1]
        else:
          winnerIdx = 1
          winnerGFlops = self.data[problemSerial+1]
          secondIdx = 0
          secondGFlops = self.data[problemSerial+0]
        for solutionIdx in range(2, self.numSolutions):
          solutionSerialIdx = problemSerial + solutionIdx
          solutionGFlops = self.data[solutionSerialIdx]
          if solutionGFlops > winnerGFlops:
            secondIdx = winnerIdx
            secondGFlops = winnerGFlops
            winnerIdx = solutionIdx
            winnerGFlops = solutionGFlops


        if winnerIdx not in winnerIndices:
          winnerIndices.append(winnerIdx)
        w += ",%4u" % winnerIdx
        g += ",%f" % winnerGFlops
        f += ",%f" % (winnerGFlops/secondGFlops)
        s += ",%4u" % (secondIdx)
      w += "\n"
      g += "\n"
      f += "\n"
      s += "\n"
      for sIdx in range(0, self.numSolutions):
        sss[sIdx] += "\n"

    w += "\n\n"
    g += "\n\n"
    f += "\n\n"
    s += "\n\n"
    for sIdx in range(0, self.numSolutions):
      sss[sIdx] += "\n\n"
    w += "Winners:\n"
    for winnerIdx in winnerIndices:
      w += "%4u, %s, %s\n" % (winnerIdx, self.solutionTiles[winnerIdx], self.solutionNames[winnerIdx])

    printFileName = "Winner2D"
    for idx in indices:
      printFileName += "_%u" % idx
    printFileName += ".csv"
    printFile = open(os.path.join(globalParameters["WorkingPath"], printFileName), "w")
    printFile.write( w )
    printFile.write( g )
    printFile.write( f )
    printFile.write( s )
    for sIdx in range(0, self.numSolutions):
      printFile.write( sss[sIdx] )
    printFile.close()


  ##############################################################################
  # Least Important Solution
  ##############################################################################
  def leastImportantSolution(self):
    solutionImportance = []
    for i in range(0, self.numSolutions):
      solutionImportance.append([i, 0, 0, 0])
    problemSizes = [0]*self.numIndices
    totalSavedMs = 0
    totalExecMs = 0
    totalWins = 0
    for problemIndices in self.problemIndicesForGlobalRange:
      for i in range(0, self.numIndices):
        problemSizes[i] = self.problemIndexToSize[i][problemIndices[i]]
      totalFlops = self.flopsPerMac
      for size in problemSizes:
        totalFlops *= size

      problemSerial = self.indicesToSerial(0, problemIndices)
      if self.data[problemSerial+0] > self.data[problemSerial+1]:
        winnerIdx = 0
        winnerGFlops = self.data[problemSerial+0]
        secondIdx = 1
        secondGFlops = self.data[problemSerial+1]
      else:
        winnerIdx = 1
        winnerGFlops = self.data[problemSerial+1]
        secondIdx = 0
        secondGFlops = self.data[problemSerial+0]

      for solutionIdx in range(2, self.numSolutions):
        solutionSerialIdx = problemSerial + solutionIdx
        solutionGFlops = self.data[solutionSerialIdx]
        if solutionGFlops > winnerGFlops:
          secondIdx = winnerIdx
          secondGFlops = winnerGFlops
          winnerIdx = solutionIdx
          winnerGFlops = solutionGFlops
      winnerTimeMs = totalFlops / winnerGFlops / 1000000
      secondTimeMs = totalFlops / secondGFlops / 1000000
      solutionImportance[winnerIdx][1] += (secondTimeMs - winnerTimeMs)
      solutionImportance[winnerIdx][2] += 1
      solutionImportance[winnerIdx][3] += winnerTimeMs

      totalSavedMs += secondTimeMs - winnerTimeMs
      totalExecMs += winnerTimeMs
      totalWins += 1
    solutionImportance.sort(key=lambda x: x[1])
    return ( solutionImportance[0][0], \
        solutionImportance[0][1] / totalSavedMs, \
        solutionImportance[0][2] / totalWins, \
        solutionImportance[0][3] / totalExecMs )


  ##############################################################################
  # Remove Solution
  ##############################################################################
  def removeSolution(self, removeSolutionIdx):

    # temporarily move current to old
    oldSolutions = self.solutions
    oldNumSolutions = self.numSolutions
    oldData = self.data
    oldTotalSize = self.totalSize

    # update solutions
    self.solutions = []
    for i in range(0, oldNumSolutions):
      if i != removeSolutionIdx:
        self.solutions.append(oldSolutions[i])
    self.solutionMinNaming = Solution.getMinNaming(self.solutions)
    self.solutionNames = []
    self.solutionTiles = []
    for solution in self.solutions:
      self.solutionNames.append(Solution.getNameMin(solution, \
          self.solutionMinNaming))
      self.solutionTiles.append("%ux%u"%(solution["MacroTile0"], \
          solution["MacroTile1"]))
    self.numSolutions = len(self.solutions)

    # update data
    self.totalSize = self.totalProblems * self.numSolutions
    self.data = array.array('f', [0]*self.totalSize)
    for problemIndex in range(0, self.totalProblems):
      newSolutionIdx = 0
      for oldSolutionIdx in range(0, oldNumSolutions):
        if oldSolutionIdx != removeSolutionIdx:
          self.data[problemIndex*self.numSolutions+newSolutionIdx] \
              = oldData[problemIndex*oldNumSolutions+oldSolutionIdx]
          newSolutionIdx += 1


  ##############################################################################
  # Score Range For Logic
  ##############################################################################
  def scoreRangeForLogic(self, indexRange, logic):
    depth = self.getLogicDepth(logic)
    depth = self.numIndices - depth
    fullLogic = deepcopy(logic)
    for i in range(0, depth):
      fullLogic = [[-1, fullLogic]]
    fullLogic = fullLogic
    return self.scoreRangeForFullLogic(depth, indexRange, fullLogic)

  ##############################################################################
  # Score Range For Full Logic
  ##############################################################################
  def scoreRangeForFullLogic(self, depth, indexRange, logic):
    score = 0
    for problemIndices in self.problemIndicesForRange(indexRange):
      problemSerial = self.indicesToSerial(0, problemIndices)
      totalFlops = self.totalFlopsForProblemIndices(problemIndices)
      solutionIdx = self.getSolutionForProblemIndicesUsingLogic( \
          problemIndices, logic)
      gflops = self.data[problemSerial + solutionIdx]
      timeUs = totalFlops / gflops / 1000
      score += timeUs
    return score

  ##############################################################################
  # Get Solution For Problem Indices Using Logic
  ##############################################################################
  def getSolutionForProblemIndicesUsingLogic(self, problemIndices, logic):
    currentProblemIndices = self.toIndexOrder(problemIndices)
    currentLogic = logic
    for i in range(0, self.numIndices):
      currentSizeIndex = currentProblemIndices[0]
      for j in range(0, len(currentLogic)):
        if currentLogic[j][0] < 0:
          currentProblemIndices = currentProblemIndices[1:]
          currentLogic = currentLogic[j][1]
          break
        if currentLogic[j][0] >= 0:
          if currentSizeIndex <= currentLogic[j][0]:
            currentProblemIndices = currentProblemIndices[1:]
            currentLogic = currentLogic[j][1]
            break
    return currentLogic


  ##############################################################################
  ##############################################################################
  ###
  ###  Helper / Low-Level Functions
  ###
  ##############################################################################
  ##############################################################################


  ##############################################################################
  # Get Winner For Problem
  def getWinnerForProblem(self, problemIndices):
    problemSerial = self.indicesToSerial(0, problemIndices)
    winnerIdx = -1
    winnerGFlops = -1
    for solutionIdx in range(0, self.numSolutions):
      solutionSerialIdx = problemSerial + solutionIdx
      solutionGFlops = self.data[solutionSerialIdx]
      if solutionGFlops > winnerGFlops:
        winnerIdx = solutionIdx
        winnerGFlops = solutionGFlops
    return (winnerIdx, winnerGFlops)


  ##############################################################################
  # Winner For Range
  def winnerForRange(self, indexRange):
    scores = self.scoreRangeForSolutions(indexRange)
    winnerIdx = 0
    for solutionIdx in range(1, self.numSolutions):
      if scores[solutionIdx] < scores[winnerIdx]:
        winnerIdx = solutionIdx
    return winnerIdx


  ##############################################################################
  # Score (microseconds) Range For Solutions
  def scoreRangeForSolutions(self, indexRange):
    scores = [0]*self.numSolutions
    for problemIndices in self.problemIndicesForRange(indexRange):
      problemSerial = self.indicesToSerial(0, problemIndices)
      totalFlops = self.totalFlopsForProblemIndices(problemIndices)
      for solutionIdx in range(0, self.numSolutions):
        gflops = self.data[problemSerial+solutionIdx]
        timeUs = totalFlops / gflops / 1000
        scores[solutionIdx] += timeUs
    return scores


  ##############################################################################
  # Score Logic Complexity
  def scoreLogicComplexity(self, logic, logicComplexity):
    depth = self.getLogicDepth(logic)
    if depth == 0: return
    depth = self.numIndices - depth
    currentLogic = logic
    for i in range(0, len(logic)):
      logicComplexity[depth] += 1
      self.scoreLogicComplexity(logic[i][1], logicComplexity)


  ##############################################################################
  # Get Logic Depth
  def getLogicDepth(self, logic):
    obj = logic
    depth = 0
    while isinstance(obj, list):
      obj = obj[0][1]
      depth += 1
    return depth


  ##############################################################################
  # To Index Order
  def toIndexOrder(self, problemIndices):
    ordered = []
    for i in self.indexOrder:
      ordered.append(problemIndices[i])
    return ordered


  ##############################################################################
  # Total Flops For Problem Indices
  def totalFlopsForProblemIndices(self, problemIndices):
    totalFlops = self.flopsPerMac
    for i in range(0, self.numIndices):
      totalFlops *= self.problemIndexToSize[i][problemIndices[i]]
    return totalFlops


  ##############################################################################
  # Recommended Index Order
  # TODO, this may depend on transposes
  def recommendedIndexOrder(self):
    order = []
    for i in range(0, self.numIndices):
      if i != self.idxU and i != self.idx1 and i != self.idx0:
        order.append(i)
    order.append(self.idxU)
    order.append(self.idx0)
    order.append(self.idx1)
    return order

  ##############################################################################
  # Problem Indices For Range
  def problemIndicesForRange(self, indexRange):
    problemIndexList = []
    problemIndices = []
    for idx in indexRange:
      problemIndices.append(idx[0])
    moreProblems = True
    while moreProblems:
      problemIndexList.append(deepcopy(problemIndices))
      # next problem
      problemIndices[0] += 1
      for i in range(0, self.numIndices):
        if problemIndices[i] >= indexRange[i][1]:
          if i == self.numIndices-1:
            moreProblems = False
            break
          else:
            problemIndices[i] = indexRange[i][0]
            problemIndices[i+1] += 1
        else:
          break
    return problemIndexList


  ##############################################################################
  # Get Size Free
  def getSizeFree(self, problemIndices):
    sizeFree = 1
    for i in self.rangeIndicesFree:
      sizeFree *= self.problemIndexToSize[i][problemIndices[i]]
    return sizeFree


  ##############################################################################
  # Get Size Summation
  def getSizeSummation(self, problemIndices):
    sizeSummation = 1
    for i in self.rangeIndicesSummation:
      sizeSummation *= self.problemIndexToSize[i][problemIndices[i]]
    return sizeSummation


  ##############################################################################
  # Get Item
  def __getitem__(self, indexTuple):
    indices = indexTuple[0] # in analysis order
    solutionIdx = indexTuple[1]
    serial = self.indicesToSerial(solutionIdx, indices)
    return self.data[serial]


  ##############################################################################
  # Set Item
  def __setitem__(self, indexTuple, value):
    indices = indexTuple[0] # in analysis order
    solutionIdx = indexTuple[1]
    serial = self.indicesToSerial(solutionIdx, indices )
    self.data[serial] = value


  ##############################################################################
  # Indices -> Serial
  def indicesToSerial(self, solutionIdx, indices ):
    serial = 0
    stride = 1
    serial += solutionIdx * stride
    stride *= self.numSolutions
    for i in range(0, self.numIndices):
      serial += indices[i] * stride
      stride *= self.numProblemSizes[i]
    return serial



################################################################################
################################################################################
###
###   Main
###
################################################################################
################################################################################
def main(  config ):
  print2("# LibraryLogic config: %s" % config)
  print2("# DefaultAnalysisParameters: " % defaultAnalysisParameters)
  benchmarkDataPath = os.path.join(globalParameters["WorkingPath"], \
      globalParameters["BenchmarkDataPath"])
  pushWorkingPath(globalParameters["LibraryLogicPath"])

  # Assign Defaults
  analysisParameters = {}
  for parameter in defaultAnalysisParameters:
    assignParameterWithDefault(analysisParameters, parameter, config, \
        defaultAnalysisParameters)

  print1("")
  print1(HR)
  print1("# Analysing data in %s" % globalParameters["BenchmarkDataPath"])
  for parameter in analysisParameters:
    print2("#   %s: %s" % (parameter, analysisParameters[parameter]))
  print1(HR)
  print1("")



  ##############################################################################
  # Determine Which Problem Types
  ##############################################################################
  problemTypeTuples = []
  if not os.path.exists(benchmarkDataPath):
    printExit("Path doesn't exist: %s" % benchmarkDataPath)
  for fileName in os.listdir(benchmarkDataPath):
    if os.path.splitext(fileName)[1] == ".csv":
      fileBase = os.path.splitext( \
          os.path.join(benchmarkDataPath, \
          fileName))[0]
      dataFileName = fileBase + ".csv"
      solutionsFileName = fileBase + ".yaml"
      if not os.path.exists(dataFileName):
        printExit("%s doesn't exist for %s" % (dataFileName, fileBase) )
      if not os.path.exists(solutionsFileName):
        printExit("%s doesn't exist for %s" % (solutionsFileName, fileBase) )
      (problemSizes, solutions) = YAMLIO.readSolutions(solutionsFileName)
      if len(solutions) == 0:
        printExit("%s doesn't contains any solutions." % (solutionsFileName) )
      problemType = solutions[0]["ProblemType"]
      problemTypeTuple = ( problemType, problemSizes, \
          dataFileName, solutionsFileName)
      if problemTypeTuple not in problemTypeTuples:
        problemTypeTuples.append(problemTypeTuple)

  # Run Analysis
  schedulePrefix = globalParameters["Name"]
  for problemTypeTuple in problemTypeTuples:
    logicTuple = analyzeProblemType( problemTypeTuple, analysisParameters )
    YAMLIO.writeLibraryLogicForProblemType(globalParameters["WorkingPath"], \
        schedulePrefix, logicTuple)

  popWorkingPath()

########################################
# TODO
# - different weights for different levels?
#   are there pairs of weights that would result in same logic complexity but better score?

########################################
# TODO problems which this algorithm
# - barrier to switching may not always be amortised on next step, need to calculate several steps into future to see if net win; process needs to be a search tree. 32x32 search only takes 1 second