"""Fresh evaluation wording, kept out of both the operation index and threshold calibration."""
PHRASES={
 "copy_title":(
  "Copy Summary!A1 from {x} into the first slide's title in {p}.",
  "Make the cover slide heading in {p} identical to the text at Summary A1 in {x}.",
  "For the heading on slide one, use the text from A1 on the workbook's Summary tab.",
  "The presentation opens with a title. Set it to the value in Summary sheet cell A1."),
 "copy_total":(
  "Show the value of Summary!B2 from {x} as the subtitle beneath the cover title in {p}.",
  "For slide one, replace its subtitle with just the number stored in the Summary tab at B2.",
  "Put the workbook's Summary B2 total on the subtitle line of the opening slide.",
  "Update the cover slide subtitle to contain the figure from B2 on the Summary worksheet."),
 "top_region":(
  "Title slide two with the best-selling region in {x}, using revenue summed across all months.",
  "Which region has the most revenue in aggregate? Use its name for the heading on slide 2.",
  "On the second slide of {p}, the title should name whichever region leads on total revenue.",
  "Group the Data tab by region, sum revenue, and put the winning region in the title of slide two."),
 "top3_list":(
  "Rank regions in {x} by total revenue. Put the top three names on separate lines in slide two's body.",
  "In descending order, list the three strongest regions by revenue, one name per line on the second slide.",
  "Replace slide 2 body text with the three regions with the greatest total revenue, largest first, one per line.",
  "Use three lines in the body of slide two for the top three regions, sorted by total revenue high to low."),
 "compute_total":(
  "Use Total: followed by the grand sum of revenue as the entire body on the second slide.",
  "In slide 2's body put 'Total: ' followed by every Data row's Revenue added together.",
  "Calculate total revenue across all regions and months, then enter Total: and the result in slide two's body.",
  "Replace the second slide body with the text 'Total: ' and the sum of the revenue column."),
 "region_table":(
  "Add a Region and Total table to slide two with one row per region and its combined revenue.",
  "Use slide 2 for a two-column table of each region and the sum of its revenue over all months.",
  "On the second slide insert a table headed Region and Total showing aggregate revenue per region.",
  "Summarize Data in a slide-two table. Use Region and Total as the headings and sum revenue within each region."),
 "next_steps":(
  "Append a slide titled Next Steps. Its text should say Grow followed by the region with the lowest revenue total.",
  "At the end of the deck insert Next Steps, with Grow and the name of the lowest-grossing region as its body.",
  "Add one final slide named Next Steps. Its body reads Grow then the name of the region with the least revenue.",
  "Finish {p} with a Next Steps slide that says Grow and names the lowest region by total sales."),
 "paste_chart":(
  "On the third slide add a chart of monthly revenue for the {extra} region, keeping months in calendar order.",
  "Use slide 3 to plot the revenue by month for the {extra} region. Show the months in time order.",
  "Create a chart for the {extra} region on slide three that presents revenue in monthly sequence.",
  "Add a monthly revenue chart to slide 3 for the {extra} region, ordered chronologically.")}
NEGATIVES=(
 "Put the Summary A1 heading in the speaker notes of slide 1.",
 "Delete the chart on slide three for the {extra} region.",
 "List the three lowest-revenue regions on slide two in ascending order.",
 "Keep the cover slide title as it is. Only tell me what is in Summary!A1.",
 "Put the monthly profit series, instead of revenue, in a chart on slide three for the {extra} region.",
 "Create a table on slide two with one row per month rather than one per region.",
 "Add a final Next Steps slide that says Sell and names the region with the highest total.",
 "Put the grand revenue total on slide four as a subtitle.",
 "Write the text in Summary!B2 into an email to finance.",
 "Remove the subtitle from slide one, leaving it blank.",
 "Produce a list of the top five regions by revenue rather than the top three on slide two.",
 "Copy the Summary A1 heading over every slide title in the entire deck.")
